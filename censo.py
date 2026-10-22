#!/usr/bin/env python
"""Pre-voo: mede na origem o que o DDL nao responde, antes de migrar nada.

Somente leitura. Nao escreve em nenhuma das duas bases.

    python censo.py --cad-origem 57

Responde, com numeros reais do recorte:
  - quantos imoveis e quantas fotos entram
  - quantas fotos orfas existem (nao migram)
  - quantas datas '0000-00-00'
  - quais valores os booleanos de texto realmente assumem
  - quantos valores monetarios nao sao numero
  - quantos textos tem sinal de double-encoding (UTF-8 gravado em latin1)
  - se ImoRef colide dentro do recorte
"""

import argparse

from migracao import config
from migracao.cadastro import COLUNAS_CONEXAO, escolher_chave
from migracao.db import conectar, consultar, um_valor


def _tem_tabela(conexao, nome):
    return bool(consultar(
        conexao,
        "SELECT 1 FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (nome,),
    ))

# Marcadores de UTF-8 lido como latin1: 'Ã', 'Â', 'â€' aparecem no lugar de acento.
PADROES_MOJIBAKE = ("Ã", "Â", "â€")

COLUNAS_BOOLEANAS = [
    "ImoLiberado", "ImoDestaque", "ImoMobiliado", "ImoSemiMobiliado",
    "ImoCozinhaPlanejada", "ImoEsquina", "ImoCosinha", "ImoOferta",
    "ImoPiscina", "ImoMurado", "ImoGrade", "ImoChurrasqueira", "ImoSacada",
    "ImoGesso", "ImoSauna", "ImoAcademia", "ImoElevador", "ImoSituacao",
]

COLUNAS_VALOR = [
    "ImoValor", "ImoValorLoc", "ImoValorHe", "ImoValorAl", "ImoIptu",
    "ImoCondominio", "ImoAreaTotal", "ImoAreaUtil", "ImoAreaConstruida",
    "ImoAreaTerreno",
]

COLUNAS_DATA = [
    "ImoAngariacao", "ImoFimExibicao", "ImoDtInsert",
    "ImoDtEnviaOlx", "ImoDtAtualizaOlx", "ImoAtualizacao",
]

COLUNAS_TEXTO = [
    "ImoTitulo", "ImoBairro", "ImoEndereco", "ImoObs",
    "ImoDescricao2", "ImoLocalizacao", "ImoDescricao",
]


def secao(titulo):
    print("\n" + titulo)
    print("-" * len(titulo))


def main():
    p = argparse.ArgumentParser(description="Censo antes da migracao (somente leitura).")
    p.add_argument("--cad-origem", type=int, required=True,
                   help="CadCodigo do cliente na origem.")
    p.add_argument("--amostra", type=int, default=12, help="Valores distintos mostrados por coluna.")
    p.add_argument("--so-origem", action="store_true",
                   help="Nao consultar o destino (pula a checagem de cliente ja existente).")
    p.add_argument("--env", default=None)
    args = p.parse_args()

    config.carregar_env(args.env)
    par = config.parametros("ORIGEM")
    con = conectar(par, autocommit=True, rotulo="origem")
    cad = args.cad_origem

    con_destino = None
    if not args.so_origem:
        con_destino = conectar(config.parametros("DESTINO"), autocommit=True,
                               rotulo="destino")

    try:
        print("=" * 72)
        print(f"CENSO -- {par['database']} @ {par['host']} -- CadCodigo = {cad}")
        print("=" * 72)

        # ------------------------------------------------------- cadastro
        secao("0. Cadastro da imobiliaria (tcadastro)")
        linhas = consultar(con, "SELECT * FROM `tcadastro` WHERE CadCodigo = %s", (cad,))
        if not linhas:
            print(f"Nenhum cadastro com CadCodigo = {cad}. Confira o codigo.")
            return 1
        cadastro_origem = linhas[0]

        for campo in ("CadNome", "CadTituloCurto", "CadCpfCnpj", "CadCreci",
                      "CadCidade", "CadEstado", "CadEmail", "CadAtivo", "CadLiberado"):
            print(f"{campo:<18} {cadastro_origem.get(campo)!r}")

        coluna_chave, valor_chave = escolher_chave(cadastro_origem)
        print(f"\nchave natural disponivel: {coluna_chave} = {valor_chave!r}")
        if not coluna_chave:
            print("ATENCAO: nenhuma chave natural util. A protecao contra cadastro")
            print("  duplicado fica dependendo so do mapa, e tcadastro e MyISAM.")

        preenchidas = [c for c in COLUNAS_CONEXAO
                       if (cadastro_origem.get(c) or "").strip()]
        if preenchidas:
            print(f"\nATENCAO: {len(preenchidas)} coluna(s) de conexao de banco preenchidas:")
            print("  " + ", ".join(preenchidas) + "  (valores omitidos de proposito)")
            print("  Copiadas cruas, o cadastro migrado aponta para o servidor ANTIGO.")
            print("  Use --limpar-conexao-banco no migrar.py se for o caso.")

        # ------------------------------------- esse cliente ja esta no destino?
        if con_destino and coluna_chave:
            secao("0b. Esse cliente ja existe no destino?")
            achados = consultar(
                con_destino,
                f"SELECT CadCodigo, CadNome FROM `tcadastro` WHERE `{coluna_chave}` = %s "
                "ORDER BY CadCodigo LIMIT 5",
                (valor_chave,),
            )
            if achados:
                print(f"SIM -- {len(achados)} cadastro(s) com {coluna_chave} = {valor_chave!r}:")
                for linha in achados:
                    print(f"  CadCodigo {linha['CadCodigo']}: {linha['CadNome']!r}")
                print("\nO migrar.py vai REUSAR esse codigo em vez de criar outro.")
                if len(achados) > 1:
                    print("MAS: mais de um achado e ambiguidade -- o migrar.py vai abortar.")
                    print("Resolva no destino ou escolha outra --chave-natural.")
            else:
                print(f"NAO -- nenhum cadastro no destino com {coluna_chave} = {valor_chave!r}.")
                print("O migrar.py vai INSERIR um novo e usar o CadCodigo gerado.")

            ja_mapeado = consultar(
                con_destino,
                "SELECT cad_codigo_destino FROM `_mig_mapa_cadastro` "
                "WHERE cad_codigo_origem = %s",
                (cad,),
            ) if _tem_tabela(con_destino, "_mig_mapa_cadastro") else []
            if ja_mapeado:
                print(f"\nJa existe mapeamento: destino CadCodigo "
                      f"{ja_mapeado[0]['cad_codigo_destino']}.")

        # ---------------------------------------------------------- volume
        secao("1. Volume do recorte")
        imoveis = um_valor(con, "SELECT COUNT(*) FROM `timoveis` WHERE ImoCadCodigo = %s", (cad,))
        print(f"imoveis: {imoveis}")
        if not imoveis:
            print("\nRecorte vazio. Confira o ImoCadCodigo.")
            return 1

        fotos = um_valor(
            con,
            "SELECT COUNT(*) FROM `tfotosimoveis` f "
            "JOIN `timoveis` i ON i.ImoCodigo = f.FotImoCodigo "
            "WHERE i.ImoCadCodigo = %s",
            (cad,),
        )
        print(f"fotos vinculadas: {fotos}")
        print(f"media de fotos por imovel: {fotos / imoveis:.1f}")

        sem_foto = um_valor(
            con,
            "SELECT COUNT(*) FROM `timoveis` i WHERE i.ImoCadCodigo = %s "
            "AND NOT EXISTS (SELECT 1 FROM `tfotosimoveis` f WHERE f.FotImoCodigo = i.ImoCodigo)",
            (cad,),
        )
        print(f"imoveis sem nenhuma foto: {sem_foto}")

        # ------------------------------------------------- fotos orfas
        secao("2. Fotos orfas (nao migram)")
        orfas = um_valor(
            con,
            "SELECT COUNT(*) FROM `tfotosimoveis` f "
            "LEFT JOIN `timoveis` i ON i.ImoCodigo = f.FotImoCodigo "
            "WHERE i.ImoCodigo IS NULL",
        )
        print(f"fotos cujo FotImoCodigo nao existe em timoveis (base inteira): {orfas}")

        # --------------------------------------------- contadores em cache
        secao("3. Contadores desnormalizados (ImoFotos / ImoFotoDestaque)")
        divergente = um_valor(
            con,
            "SELECT COUNT(*) FROM ("
            "  SELECT i.ImoCodigo FROM `timoveis` i"
            "  LEFT JOIN `tfotosimoveis` f ON f.FotImoCodigo = i.ImoCodigo"
            "  WHERE i.ImoCadCodigo = %s"
            "  GROUP BY i.ImoCodigo, i.ImoFotos"
            "  HAVING i.ImoFotos <> COUNT(f.FotCodigo)) AS d",
            (cad,),
        )
        print(f"imoveis em que ImoFotos JA MENTE na origem: {divergente}")
        print("  (por isso o migrador recalcula em vez de copiar)")

        destaque_ruim = um_valor(
            con,
            "SELECT COUNT(*) FROM `timoveis` i "
            "LEFT JOIN `tfotosimoveis` f ON f.FotCodigo = i.ImoFotoDestaque "
            "WHERE i.ImoCadCodigo = %s AND i.ImoFotoDestaque <> 0 "
            "AND (f.FotCodigo IS NULL OR f.FotImoCodigo <> i.ImoCodigo)",
            (cad,),
        )
        print(f"imoveis com ImoFotoDestaque invalido ou de outro imovel: {destaque_ruim}")

        # ------------------------------------------------------- datas zero
        secao("4. Datas '0000-00-00'")
        for coluna in COLUNAS_DATA:
            total = um_valor(
                con,
                f"SELECT COUNT(*) FROM `timoveis` "
                f"WHERE ImoCadCodigo = %s AND CAST(`{coluna}` AS CHAR) = '0000-00-00'",
                (cad,),
            )
            marca = "  <-- precisa de sql_mode permissivo" if total else ""
            print(f"{coluna:<20} {total:>8}{marca}")

        secao("5. ImoAngariacao (date) x ImoDtAngaria (varchar)")
        print("Qual das duas e confiavel? Amostra de divergencia:")
        linhas = consultar(
            con,
            "SELECT ImoCodigo, CAST(ImoAngariacao AS CHAR) AS data_date, ImoDtAngaria "
            "FROM `timoveis` WHERE ImoCadCodigo = %s AND TRIM(ImoDtAngaria) <> '' "
            "LIMIT 8",
            (cad,),
        )
        for linha in linhas:
            print(f"  ImoCodigo {linha['ImoCodigo']}: date={linha['data_date']!r} "
                  f"varchar={linha['ImoDtAngaria']!r}")
        if not linhas:
            print("  (ImoDtAngaria vazio em todo o recorte)")

        # ---------------------------------------------------- booleanos
        secao("6. Valores reais dos booleanos de texto")
        for coluna in COLUNAS_BOOLEANAS:
            linhas = consultar(
                con,
                f"SELECT `{coluna}` AS v, COUNT(*) AS t FROM `timoveis` "
                f"WHERE ImoCadCodigo = %s GROUP BY `{coluna}` "
                f"ORDER BY t DESC LIMIT {int(args.amostra)}",
                (cad,),
            )
            valores = ", ".join(f"{l['v']!r}={l['t']}" for l in linhas)
            print(f"{coluna:<22} {valores}")

        # ------------------------------------------------------- valores
        secao("7. Campos monetarios / area que nao sao numero")
        print("(nao vazio e que nao casa com numero simples)")
        for coluna in COLUNAS_VALOR:
            total = um_valor(
                con,
                f"SELECT COUNT(*) FROM `timoveis` WHERE ImoCadCodigo = %s "
                f"AND TRIM(`{coluna}`) <> '' "
                f"AND `{coluna}` NOT REGEXP '^[0-9]+([.,][0-9]+)*$'",
                (cad,),
            )
            print(f"{coluna:<22} {total:>8}")
            if total:
                exemplos = consultar(
                    con,
                    f"SELECT DISTINCT `{coluna}` AS v FROM `timoveis` WHERE ImoCadCodigo = %s "
                    f"AND TRIM(`{coluna}`) <> '' "
                    f"AND `{coluna}` NOT REGEXP '^[0-9]+([.,][0-9]+)*$' LIMIT 5",
                    (cad,),
                )
                print("      exemplos: " + ", ".join(repr(e["v"]) for e in exemplos))

        # ------------------------------------------------------ encoding
        secao("8. Sinais de double-encoding (UTF-8 gravado em coluna latin1)")
        print("Se der > 0, o texto JA esta corrompido na origem. A migracao copia")
        print("fielmente; corrigir e decisao separada, nao se resolve copiando.")
        for coluna in COLUNAS_TEXTO:
            clausulas = " OR ".join([f"`{coluna}` LIKE %s"] * len(PADROES_MOJIBAKE))
            total = um_valor(
                con,
                f"SELECT COUNT(*) FROM `timoveis` WHERE ImoCadCodigo = %s AND ({clausulas})",
                (cad, *[f"%{p}%" for p in PADROES_MOJIBAKE]),
            )
            print(f"{coluna:<20} {total:>8}")

        # --------------------------------------------------------- ImoRef
        secao("9. ImoRef dentro do recorte")
        vazias = um_valor(
            con,
            "SELECT COUNT(*) FROM `timoveis` WHERE ImoCadCodigo = %s AND TRIM(ImoRef) = ''",
            (cad,),
        )
        print(f"imoveis com ImoRef vazia: {vazias}")
        dup = consultar(
            con,
            "SELECT ImoRef, COUNT(*) AS t FROM `timoveis` WHERE ImoCadCodigo = %s "
            "AND TRIM(ImoRef) <> '' GROUP BY ImoRef HAVING t > 1 ORDER BY t DESC LIMIT 10",
            (cad,),
        )
        if dup:
            print(f"ATENCAO: ImoRef repetida DENTRO do recorte ({len(dup)} valores):")
            for linha in dup:
                print(f"  {linha['ImoRef']!r} x{linha['t']}")
            print("  --checar-ref vai pular as repetidas. Decida a regra antes de migrar.")
        else:
            print("ImoRef unica dentro do recorte: OK")

        # ---------------------------------------------- status do recorte
        secao("10. Situacao dos imoveis do recorte")
        for coluna in ("ImoStatus", "ImoVendido", "ImoAlugado", "ImoTeste", "ImoLiberado"):
            linhas = consultar(
                con,
                f"SELECT `{coluna}` AS v, COUNT(*) AS t FROM `timoveis` WHERE ImoCadCodigo = %s "
                f"GROUP BY `{coluna}` ORDER BY t DESC LIMIT 8",
                (cad,),
            )
            print(f"{coluna:<14} " + ", ".join(f"{l['v']!r}={l['t']}" for l in linhas))
        com_dtdel = um_valor(
            con,
            "SELECT COUNT(*) FROM `timoveis` WHERE ImoCadCodigo = %s AND ImoDtDel IS NOT NULL",
            (cad,),
        )
        print(f"com ImoDtDel preenchido (excluido logico): {com_dtdel}")

        print("\n" + "=" * 72)
        print("Censo concluido. Nada foi alterado.")
        return 0

    finally:
        con.close()
        if con_destino:
            con_destino.close()


if __name__ == "__main__":
    raise SystemExit(main())
