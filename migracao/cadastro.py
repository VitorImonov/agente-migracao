"""Migracao do cadastro da imobiliaria (tcadastro), primeiro elo da cadeia.

    tcadastro      CadCodigo 57 -> 912   (lastrowid)
    timoveis       ImoCadCodigo = 912
    tfotosimoveis  FotImoCodigo = <ImoCodigo novo>

O PROBLEMA DESTE MODULO: tcadastro e MyISAM.

MyISAM nao tem transacao. O INSERT commita no ato e ROLLBACK nao desfaz. Logo a
protecao usada nos imoveis -- "imovel e mapa na mesma transacao" -- nao existe
aqui. Se o processo morrer entre o INSERT do cadastro e o INSERT no mapa, sobra
um cadastro sem mapeamento; a reexecucao criaria a imobiliaria DE NOVO, e os
imoveis da segunda rodada iriam para um cliente fantasma.

A protecao usada no lugar e CHAVE NATURAL: antes de inserir, procura-se no
destino um cadastro que ja seja esse mesmo cliente (CadUsuario, que e o login e
tem indice, ou CadCpfCnpj). Se achar, reusa o CadCodigo em vez de inserir.
Isso torna a operacao idempotente sem depender de transacao nem do mapa.

Ordem das operacoes, e o porque:

  1. mapa  -> achou? reusa. (caminho normal da reexecucao)
  2. chave natural no destino -> achou? reusa e CONSERTA o mapa.
     (caminho de recuperacao da janela MyISAM)
  3. nada achado -> INSERT, pega lastrowid, grava o mapa IMEDIATAMENTE.
  4. se o passo 3 inserir e o mapa falhar, o cadastro orfao e denunciado com o
     CadCodigo na mao e a execucao para. Nao se migra imovel para cadastro nao
     mapeado.
"""

from . import controle
from .controle import OC_CADASTRO_CONEXAO, OC_CADASTRO_ORFAO, OC_CADASTRO_REUSADO

# Credenciais de conexao do banco DO CLIENTE. Copiadas cruas, o cadastro
# migrado continua apontando para o servidor antigo.
COLUNAS_CONEXAO = ("CadIpBanco", "CadIpUsuario", "CadIpSenha", "CadIpBase")

# Nunca imprimir em log, relatorio ou censo.
COLUNAS_SENSIVEIS = frozenset(
    {"CadIpSenha", "CadIpUsuario", "CadIpBanco", "CadIpBase",
     "CadSenha", "CadUsuario", "CadToken", "CadRecap"}
)

# Candidatas a chave natural, em ordem de preferencia.
# CadUsuario e o login e tem indice (KEY cadusuario); dois cadastros com o
# mesmo login quebram o acesso, entao na pratica ele e unico.
CHAVES_NATURAIS = ("CadUsuario", "CadCpfCnpj", "CadEmail", "CadCreci")


class CadastroResultado:
    def __init__(self, cad_destino, reusado, motivo, chave_usada, chave_valor):
        self.cad_destino = cad_destino
        self.reusado = reusado
        self.motivo = motivo
        self.chave_usada = chave_usada
        self.chave_valor = chave_valor


def ler_origem(con_origem, cad_origem):
    with con_origem.cursor() as cur:
        cur.execute("SELECT * FROM `tcadastro` WHERE CadCodigo = %s", (cad_origem,))
        linha = cur.fetchone()
    if not linha:
        raise SystemExit(
            f"Nenhum cadastro na origem com CadCodigo = {cad_origem}. "
            "Confira o codigo do cliente."
        )
    return linha


def escolher_chave(cadastro, preferida=None):
    """Primeira chave natural com valor util. Devolve (coluna, valor)."""
    ordem = (preferida,) + CHAVES_NATURAIS if preferida else CHAVES_NATURAIS
    for coluna in ordem:
        valor = (cadastro.get(coluna) or "").strip()
        if valor and valor not in ("0", "-"):
            return coluna, valor
    return None, None


def _buscar_no_mapa(con_destino, origem, cad_origem):
    with con_destino.cursor() as cur:
        cur.execute(
            "SELECT cad_codigo_destino FROM `_mig_mapa_cadastro` "
            "WHERE origem = %s AND cad_codigo_origem = %s",
            (origem, cad_origem),
        )
        linha = cur.fetchone()
    return linha["cad_codigo_destino"] if linha else None


def _buscar_por_chave(con_destino, coluna, valor):
    """Procura o cliente no destino pela chave natural."""
    with con_destino.cursor() as cur:
        cur.execute(
            f"SELECT CadCodigo, CadNome FROM `tcadastro` WHERE `{coluna}` = %s "
            "ORDER BY CadCodigo LIMIT 2",
            (valor,),
        )
        return cur.fetchall()


def _gravar_mapa(con_destino, origem, cad_origem, cad_destino, cadastro,
                 chave_valor, execucao):
    with con_destino.cursor() as cur:
        cur.execute(
            "INSERT INTO `_mig_mapa_cadastro` "
            "(origem, cad_codigo_origem, cad_codigo_destino, chave_natural, cad_nome, execucao) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (origem, cad_origem, cad_destino, (chave_valor or "")[:100],
             (cadastro.get("CadNome") or "")[:100], execucao),
        )
    con_destino.commit()


def migrar(con_origem, con_destino, tabelas, opcoes, log):
    """Devolve CadastroResultado com o CadCodigo a usar em ImoCadCodigo."""
    origem = opcoes.rotulo_origem
    cad_origem = opcoes.cad_origem
    cadastro = ler_origem(con_origem, cad_origem)
    coluna_chave, valor_chave = escolher_chave(cadastro, opcoes.chave_natural)

    nome = cadastro.get("CadNome") or cadastro.get("CadTituloCurto") or "(sem nome)"
    log(f"cadastro de origem {cad_origem}: {nome!r}")
    if coluna_chave:
        log(f"chave natural: {coluna_chave} = {valor_chave!r}")
    else:
        log("ATENCAO: nenhuma chave natural com valor util neste cadastro.")
        log("  Sem ela, a protecao contra cadastro duplicado depende so do mapa,")
        log("  e tcadastro e MyISAM (sem transacao). Risco aceito.")

    # 1. caminho normal da reexecucao
    existente = _buscar_no_mapa(con_destino, origem, cad_origem)
    if existente:
        log(f"cadastro ja migrado: CadCodigo destino {existente} (mapa). Reusando.")
        return CadastroResultado(existente, True, "mapa", coluna_chave, valor_chave)

    # 2. recuperacao da janela MyISAM: cadastro existe, mapa nao
    if coluna_chave:
        achados = _buscar_por_chave(con_destino, coluna_chave, valor_chave)
        if len(achados) > 1:
            raise SystemExit(
                f"Mais de um cadastro no destino com {coluna_chave} = {valor_chave!r} "
                f"(CadCodigo {', '.join(str(a['CadCodigo']) for a in achados)}). "
                "Resolva a ambiguidade no destino antes de migrar, ou escolha outra "
                "chave com --chave-natural."
            )
        if achados:
            cad_destino = achados[0]["CadCodigo"]
            log(f"cadastro JA EXISTE no destino como CadCodigo {cad_destino} "
                f"(encontrado por {coluna_chave}), mas nao estava no mapa.")
            log("  Reusando e registrando no mapa. Nada foi duplicado.")
            if not opcoes.dry_run:
                _gravar_mapa(con_destino, origem, cad_origem, cad_destino,
                             cadastro, valor_chave, opcoes.execucao)
                with con_destino.cursor() as cur:
                    controle.registrar_ocorrencia(
                        cur, opcoes.execucao, None, OC_CADASTRO_REUSADO,
                        f"CadCodigo origem {cad_origem} -> destino {cad_destino} "
                        f"reencontrado por {coluna_chave}",
                    )
                con_destino.commit()
            return CadastroResultado(cad_destino, True, "chave_natural",
                                     coluna_chave, valor_chave)

    # 3. inserir
    if opcoes.dry_run:
        log("DRY-RUN: cadastro seria INSERIDO agora.")
        log("  ATENCAO: tcadastro e MyISAM. Num dry-run real o INSERT nao pode")
        log("  ser desfeito, portanto ele NAO e executado. O CadCodigo abaixo e")
        log("  ficticio, so para o ensaio dos imoveis prosseguir.")
        return CadastroResultado(-1, False, "dry_run", coluna_chave, valor_chave)

    cols = [c for c in tabelas["cadastro_destino"].colunas_para_inserir()
            if c in tabelas["cadastro_origem"].por_nome]
    limites = tabelas["cadastro_destino"].limites_texto()

    valores = []
    avisos_truncamento = []
    for coluna in cols:
        valor = cadastro[coluna]
        if opcoes.limpar_conexao_banco and coluna in COLUNAS_CONEXAO:
            valor = ""
        limite = limites.get(coluna)
        if limite and isinstance(valor, str) and len(valor) > limite:
            avisos_truncamento.append(f"{coluna}: {len(valor)} > {limite}")
        valores.append(valor)

    sql = (
        "INSERT INTO `tcadastro` ("
        + ", ".join(f"`{c}`" for c in cols)
        + ") VALUES ("
        + ", ".join(["%s"] * len(cols))
        + ")"
    )

    with con_destino.cursor() as cur:
        cur.execute(sql, valores)
        cad_destino = cur.lastrowid
    con_destino.commit()  # MyISAM: ja estava gravado de qualquer forma

    if not cad_destino:
        raise SystemExit(
            "INSERT em tcadastro nao devolveu lastrowid. Verifique manualmente se "
            "o cadastro foi criado no destino antes de rodar de novo -- tcadastro "
            "e MyISAM e o INSERT nao pode ser desfeito."
        )

    log(f"cadastro INSERIDO no destino: CadCodigo {cad_destino}")

    # 4. mapa imediatamente depois. Se falhar, denuncia o orfao e para.
    try:
        _gravar_mapa(con_destino, origem, cad_origem, cad_destino, cadastro,
                     valor_chave, opcoes.execucao)
    except Exception as erro:
        raise SystemExit(
            "O cadastro FOI CRIADO no destino como CadCodigo "
            f"{cad_destino}, mas o registro no mapa falhou ({erro}).\n"
            "tcadastro e MyISAM: esse INSERT nao pode ser desfeito automaticamente.\n"
            "Antes de rodar de novo, faca UMA das duas coisas:\n"
            f"  a) inserir o mapeamento a mao:\n"
            f"     INSERT INTO _mig_mapa_cadastro "
            f"(origem, cad_codigo_origem, cad_codigo_destino) VALUES "
            f"('{origem}', {cad_origem}, {cad_destino});\n"
            f"  b) apagar o cadastro orfao:\n"
            f"     DELETE FROM tcadastro WHERE CadCodigo = {cad_destino};\n"
            "Rodar de novo sem isso cria a imobiliaria DUPLICADA."
        )

    with con_destino.cursor() as cur:
        for aviso in avisos_truncamento:
            controle.registrar_ocorrencia(
                cur, opcoes.execucao, None, controle.OC_TRUNCAMENTO,
                f"tcadastro.{aviso}",
            )
        conexao_copiada = [
            c for c in COLUNAS_CONEXAO
            if not opcoes.limpar_conexao_banco and (cadastro.get(c) or "").strip()
        ]
        if conexao_copiada:
            controle.registrar_ocorrencia(
                cur, opcoes.execucao, None, OC_CADASTRO_CONEXAO,
                "copiadas cruas da origem: " + ", ".join(conexao_copiada),
            )
    con_destino.commit()

    if conexao_copiada:
        log("ATENCAO: " + ", ".join(conexao_copiada) + " foram copiadas da origem.")
        log("  Sao as credenciais do banco DO CLIENTE: o cadastro migrado aponta")
        log("  para o servidor ANTIGO. Ajuste no destino ou use --limpar-conexao-banco.")

    return CadastroResultado(cad_destino, False, "inserido", coluna_chave, valor_chave)


def verificar_orfaos(con_destino, origem):
    """Cadastros no destino que parecem desta migracao mas nao estao no mapa.

    Nao da para detectar com certeza (tcadastro nao tem carimbo de origem), mas
    serve de alerta sobre a janela MyISAM.
    """
    with con_destino.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS t FROM `_mig_mapa_cadastro` WHERE origem = %s",
            (origem,),
        )
        return cur.fetchone()["t"]
