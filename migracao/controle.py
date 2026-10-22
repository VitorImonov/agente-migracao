"""Tabelas de controle no destino: o mapa de IDs.

Ficam no destino, nao em arquivo local, por uma razao de correcao: o INSERT do
imovel e o INSERT no mapa acontecem na mesma transacao e na mesma conexao.
Nao existe janela em que o imovel esteja gravado e o mapeamento nao -- que e
exatamente a janela que, numa queda de energia, viraria duplicata na
reexecucao.

O prefixo _mig_ marca que sao objetos temporarios da migracao, descartaveis
depois do corte (ver limpar()).
"""

DDL = [
    # O mapa do cadastro e InnoDB mesmo que tcadastro seja MyISAM: ele e a
    # unica coisa que impede a imobiliaria de ser duplicada numa reexecucao.
    """
    CREATE TABLE IF NOT EXISTS `_mig_mapa_cadastro` (
      `origem`             varchar(40)  NOT NULL,
      `cad_codigo_origem`  int(11)      NOT NULL,
      `cad_codigo_destino` int(11)      NOT NULL,
      `chave_natural`      varchar(100) NOT NULL DEFAULT '',
      `cad_nome`           varchar(100) NOT NULL DEFAULT '',
      `execucao`           varchar(40)  NOT NULL DEFAULT '',
      `migrado_em`         datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (`origem`,`cad_codigo_origem`),
      UNIQUE KEY `uq_destino` (`cad_codigo_destino`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS `_mig_mapa_imoveis` (
      `origem`             varchar(40) NOT NULL,
      `imo_cad_codigo`     int(11)     NOT NULL COMMENT 'ImoCadCodigo no DESTINO',
      `imo_cad_origem`     int(11)     NOT NULL COMMENT 'ImoCadCodigo na ORIGEM',
      `imo_codigo_origem`  int(11)     NOT NULL,
      `imo_codigo_destino` int(11)     NOT NULL,
      `imo_ref`            varchar(20) NOT NULL DEFAULT '',
      `qtd_fotos`          int(11)     NOT NULL DEFAULT 0,
      `execucao`           varchar(40) NOT NULL DEFAULT '',
      `migrado_em`         datetime    NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (`origem`,`imo_codigo_origem`),
      UNIQUE KEY `uq_destino` (`imo_codigo_destino`),
      KEY `idx_cliente` (`imo_cad_codigo`),
      KEY `idx_execucao` (`execucao`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS `_mig_mapa_fotos` (
      `origem`             varchar(40) NOT NULL,
      `fot_codigo_origem`  int(11)     NOT NULL,
      `fot_codigo_destino` int(11)     NOT NULL,
      `imo_codigo_origem`  int(11)     NOT NULL,
      `execucao`           varchar(40) NOT NULL DEFAULT '',
      PRIMARY KEY (`origem`,`fot_codigo_origem`),
      UNIQUE KEY `uq_destino` (`fot_codigo_destino`),
      KEY `idx_imovel` (`imo_codigo_origem`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS `_mig_ocorrencia` (
      `id`                bigint(20)   NOT NULL AUTO_INCREMENT,
      `execucao`          varchar(40)  NOT NULL DEFAULT '',
      `imo_codigo_origem` int(11)      DEFAULT NULL,
      `tipo`              varchar(40)  NOT NULL,
      `detalhe`           varchar(500) NOT NULL DEFAULT '',
      `criado_em`         datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      KEY `idx_tipo` (`tipo`),
      KEY `idx_execucao` (`execucao`),
      KEY `idx_imovel` (`imo_codigo_origem`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]

# Tipos de ocorrencia registrados
OC_REF_DUPLICADA = "ref_duplicada"
OC_DESTAQUE_NAO_RESOLVIDO = "destaque_nao_resolvido"
OC_SEM_FOTOS = "sem_fotos"
OC_TRUNCAMENTO = "truncamento"
OC_DATA_ZERO = "data_zero"
OC_FALHA = "falha"
OC_CADASTRO_REUSADO = "cadastro_reusado"
OC_CADASTRO_CONEXAO = "cadastro_conexao_banco"
OC_CADASTRO_ORFAO = "cadastro_orfao"


def criar(conexao):
    """DDL nao participa de transacao: roda sempre, inclusive em dry-run."""
    with conexao.cursor() as cur:
        for comando in DDL:
            cur.execute(comando)
    conexao.commit()


def ja_migrados(conexao, origem, cad_origem):
    """IDs de origem ja mapeados. Base da idempotencia da reexecucao."""
    sql = """
        SELECT imo_codigo_origem
          FROM `_mig_mapa_imoveis`
         WHERE origem = %s AND imo_cad_origem = %s
    """
    with conexao.cursor() as cur:
        cur.execute(sql, (origem, cad_origem))
        return {linha["imo_codigo_origem"] for linha in cur.fetchall()}


def registrar_ocorrencia(cursor, execucao, imo_origem, tipo, detalhe=""):
    """Gravado no mesmo cursor/transacao do imovel: ou os dois entram, ou nenhum."""
    cursor.execute(
        "INSERT INTO `_mig_ocorrencia` (execucao, imo_codigo_origem, tipo, detalhe) "
        "VALUES (%s, %s, %s, %s)",
        (execucao, imo_origem, tipo, detalhe[:500]),
    )


def resumo_ocorrencias(conexao, execucao):
    sql = """
        SELECT tipo, COUNT(*) AS total
          FROM `_mig_ocorrencia`
         WHERE execucao = %s
         GROUP BY tipo
         ORDER BY total DESC
    """
    with conexao.cursor() as cur:
        cur.execute(sql, (execucao,))
        return cur.fetchall()


def limpar(conexao):
    """Remove as tabelas de controle. So depois do corte e da validacao."""
    with conexao.cursor() as cur:
        for tabela in (
            "_mig_ocorrencia",
            "_mig_mapa_fotos",
            "_mig_mapa_imoveis",
            "_mig_mapa_cadastro",
        ):
            cur.execute(f"DROP TABLE IF EXISTS `{tabela}`")
    conexao.commit()
