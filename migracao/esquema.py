"""Introspeccao das tabelas e conferencia de que origem e destino batem.

O usuario informou que as estruturas sao identicas. Isso e conferido em vez de
assumido: uma coluna a mais no destino, ou um varchar que encolheu, viraria
perda silenciosa de dado no meio da migracao.
"""

from .db import consultar

CONSULTA = """
SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, EXTRA,
       CHARACTER_MAXIMUM_LENGTH, DATA_TYPE
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
 ORDER BY ORDINAL_POSITION
"""


class Tabela:
    def __init__(self, nome, colunas):
        self.nome = nome
        self.colunas = colunas
        self.nomes = [c["COLUMN_NAME"] for c in colunas]
        self.por_nome = {c["COLUMN_NAME"]: c for c in colunas}

    @property
    def auto_increment(self):
        for c in self.colunas:
            if "auto_increment" in (c["EXTRA"] or ""):
                return c["COLUMN_NAME"]
        return None

    def colunas_para_inserir(self):
        """Tudo menos o AUTO_INCREMENT: o destino gera o ID novo."""
        ai = self.auto_increment
        return [n for n in self.nomes if n != ai]

    def limites_texto(self):
        """{coluna: tamanho_maximo} apenas para colunas de texto com limite."""
        return {
            c["COLUMN_NAME"]: int(c["CHARACTER_MAXIMUM_LENGTH"])
            for c in self.colunas
            if c["CHARACTER_MAXIMUM_LENGTH"] is not None
        }


def ler_tabela(conexao, schema, nome):
    colunas = consultar(conexao, CONSULTA, (schema, nome))
    if not colunas:
        raise SystemExit(f"Tabela nao encontrada: {schema}.{nome}")
    return Tabela(nome, colunas)


def ler_engine(conexao, schema, nome):
    """Engine da tabela. Determina se existe transacao -- nao e detalhe.

    timoveis e tfotosimoveis precisam ser InnoDB no destino: toda a garantia de
    'imovel e fotos entram juntos ou nao entram' e um ROLLBACK. Em MyISAM o
    ROLLBACK e silenciosamente ignorado e sobra imovel sem foto no banco.
    """
    linhas = consultar(
        conexao,
        "SELECT ENGINE FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
        (schema, nome),
    )
    return (linhas[0]["ENGINE"] or "").upper() if linhas else None


def exigir_transacional(conexao, schema, tabelas):
    """Devolve lista de tabelas que NAO suportam transacao."""
    sem_transacao = []
    for nome in tabelas:
        engine = ler_engine(conexao, schema, nome)
        if engine != "INNODB":
            sem_transacao.append((nome, engine or "desconhecida"))
    return sem_transacao


SO_ORIGEM = "so_origem"
SO_DESTINO = "so_destino"
TIPO_DIFERENTE = "tipo_diferente"


def comparar(tab_origem, tab_destino):
    """Divergencias estruturadas entre as duas pontas.

    Cada item e um dict com tabela/coluna/tipo/mensagem. A forma estruturada
    permite declarar diferencas ja revisadas (migracao.diferencas_aceitas) sem
    precisar de um '--ignorar-tudo' que tambem esconderia diferenca nova.
    """
    problemas = []
    tabela = tab_origem.nome

    for nome in tab_origem.nomes:
        if nome not in tab_destino.por_nome:
            problemas.append({
                "tabela": tabela, "coluna": nome, "tipo": SO_ORIGEM,
                "mensagem": f"{tabela}.{nome}: existe na origem e nao no destino "
                            f"({tab_origem.por_nome[nome]['COLUMN_TYPE']}) -- dado sem destino",
            })

    for nome in tab_destino.nomes:
        if nome not in tab_origem.por_nome:
            col = tab_destino.por_nome[nome]
            obrigatoria = col["IS_NULLABLE"] == "NO" and not (col["EXTRA"] or "")
            problemas.append({
                "tabela": tabela, "coluna": nome, "tipo": SO_DESTINO,
                "mensagem": f"{tabela}.{nome}: existe so no destino "
                            f"({col['COLUMN_TYPE']}, "
                            f"{'NOT NULL sem default' if obrigatoria else 'aceita default'})",
            })

    for nome in tab_origem.nomes:
        if nome not in tab_destino.por_nome:
            continue
        o = tab_origem.por_nome[nome]
        d = tab_destino.por_nome[nome]
        if o["COLUMN_TYPE"] != d["COLUMN_TYPE"]:
            problemas.append({
                "tabela": tabela, "coluna": nome, "tipo": TIPO_DIFERENTE,
                "mensagem": f"{tabela}.{nome}: tipo diferente "
                            f"(origem {o['COLUMN_TYPE']} / destino {d['COLUMN_TYPE']})",
            })
    return problemas


def colunas_so_na_origem(tab_origem, tab_destino):
    return [n for n in tab_origem.nomes if n not in tab_destino.por_nome]
