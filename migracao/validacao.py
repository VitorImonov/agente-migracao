"""Validacao pos-migracao.

Tres consultas que precisam voltar zero. Se qualquer uma devolver linha, a
relacao imovel <-> foto nao esta coerente e o corte nao deve acontecer.

A consulta B e a mais importante: ela pega o erro silencioso, a foto entregue
no imovel errado. Esse erro nao gera excecao, nao gera orfa e nao aparece em
nenhuma checagem de integridade do MySQL -- so nela.
"""

CHECAGENS = [
    {
        "id": "A",
        "titulo": "Foto apontando para imovel que nao existe no destino",
        "gravidade": "critica",
        "sql": """
            SELECT COUNT(*) AS total
              FROM `_mig_mapa_fotos` mf
              JOIN `tfotosimoveis` f ON f.FotCodigo = mf.fot_codigo_destino
              LEFT JOIN `timoveis` i ON i.ImoCodigo = f.FotImoCodigo
             WHERE mf.origem = %(origem)s
               AND i.ImoCodigo IS NULL
        """,
    },
    {
        "id": "B",
        "titulo": "Foto que trocou de dono (pai no destino != pai mapeado)",
        "gravidade": "critica",
        "sql": """
            SELECT COUNT(*) AS total
              FROM `_mig_mapa_fotos` mf
              JOIN `tfotosimoveis` f ON f.FotCodigo = mf.fot_codigo_destino
              JOIN `_mig_mapa_imoveis` mi
                    ON mi.origem = mf.origem
                   AND mi.imo_codigo_origem = mf.imo_codigo_origem
             WHERE mf.origem = %(origem)s
               AND f.FotImoCodigo <> mi.imo_codigo_destino
        """,
    },
    {
        "id": "C",
        "titulo": "Divergencia de contagem de fotos por imovel",
        "gravidade": "critica",
        "sql": """
            SELECT COUNT(*) AS total FROM (
                SELECT mi.imo_codigo_origem
                  FROM `_mig_mapa_imoveis` mi
                  LEFT JOIN `tfotosimoveis` f ON f.FotImoCodigo = mi.imo_codigo_destino
                 WHERE mi.origem = %(origem)s
                 GROUP BY mi.imo_codigo_origem, mi.qtd_fotos
                HAVING mi.qtd_fotos <> COUNT(f.FotCodigo)
            ) AS divergentes
        """,
    },
    {
        "id": "D",
        "titulo": "ImoFotos divergente da contagem real de fotos",
        "gravidade": "alta",
        "sql": """
            SELECT COUNT(*) AS total FROM (
                SELECT i.ImoCodigo
                  FROM `_mig_mapa_imoveis` mi
                  JOIN `timoveis` i ON i.ImoCodigo = mi.imo_codigo_destino
                  LEFT JOIN `tfotosimoveis` f ON f.FotImoCodigo = i.ImoCodigo
                 WHERE mi.origem = %(origem)s
                 GROUP BY i.ImoCodigo, i.ImoFotos
                HAVING i.ImoFotos <> COUNT(f.FotCodigo)
            ) AS divergentes
        """,
    },
    {
        "id": "E",
        "titulo": "ImoFotoDestaque apontando para foto de outro imovel",
        "gravidade": "alta",
        "sql": """
            SELECT COUNT(*) AS total
              FROM `_mig_mapa_imoveis` mi
              JOIN `timoveis` i ON i.ImoCodigo = mi.imo_codigo_destino
              LEFT JOIN `tfotosimoveis` f ON f.FotCodigo = i.ImoFotoDestaque
             WHERE mi.origem = %(origem)s
               AND i.ImoFotoDestaque <> 0
               AND (f.FotCodigo IS NULL OR f.FotImoCodigo <> i.ImoCodigo)
        """,
    },
    {
        "id": "F",
        "titulo": "Imovel migrado com ImoCadCodigo diferente do destino informado",
        "gravidade": "critica",
        "sql": """
            SELECT COUNT(*) AS total
              FROM `_mig_mapa_imoveis` mi
              JOIN `timoveis` i ON i.ImoCodigo = mi.imo_codigo_destino
             WHERE mi.origem = %(origem)s
               AND i.ImoCadCodigo <> mi.imo_cad_codigo
        """,
    },
    {
        "id": "H",
        "titulo": "Imovel migrado apontando para cadastro inexistente (cliente fantasma)",
        "gravidade": "critica",
        "sql": """
            SELECT COUNT(*) AS total
              FROM `_mig_mapa_imoveis` mi
              JOIN `timoveis` i ON i.ImoCodigo = mi.imo_codigo_destino
              LEFT JOIN `tcadastro` c ON c.CadCodigo = i.ImoCadCodigo
             WHERE mi.origem = %(origem)s
               AND c.CadCodigo IS NULL
        """,
    },
    {
        "id": "I",
        "titulo": "Imovel pendurado em cadastro diferente do mapeado",
        "gravidade": "critica",
        "sql": """
            SELECT COUNT(*) AS total
              FROM `_mig_mapa_imoveis` mi
              JOIN `_mig_mapa_cadastro` mc
                    ON mc.origem = mi.origem
                   AND mc.cad_codigo_origem = mi.imo_cad_origem
              JOIN `timoveis` i ON i.ImoCodigo = mi.imo_codigo_destino
             WHERE mi.origem = %(origem)s
               AND i.ImoCadCodigo <> mc.cad_codigo_destino
        """,
    },
    {
        "id": "J",
        "titulo": "Cadastro mapeado que nao existe mais no destino",
        "gravidade": "critica",
        "sql": """
            SELECT COUNT(*) AS total
              FROM `_mig_mapa_cadastro` mc
              LEFT JOIN `tcadastro` c ON c.CadCodigo = mc.cad_codigo_destino
             WHERE mc.origem = %(origem)s
               AND c.CadCodigo IS NULL
        """,
    },
    {
        "id": "G",
        "titulo": "ImoRef duplicada dentro da mesma imobiliaria no destino",
        "gravidade": "alta",
        "sql": """
            SELECT COUNT(*) AS total FROM (
                SELECT i.ImoCadCodigo, i.ImoRef
                  FROM `timoveis` i
                 WHERE i.ImoCadCodigo IN (
                        SELECT DISTINCT imo_cad_codigo FROM `_mig_mapa_imoveis`
                         WHERE origem = %(origem)s)
                   AND TRIM(i.ImoRef) <> ''
                 GROUP BY i.ImoCadCodigo, i.ImoRef
                HAVING COUNT(*) > 1
            ) AS duplicadas
        """,
    },
]


def rodar(conexao, origem):
    """Devolve lista de (checagem, total). total > 0 = problema."""
    resultados = []
    with conexao.cursor() as cur:
        for checagem in CHECAGENS:
            cur.execute(checagem["sql"], {"origem": origem})
            linha = cur.fetchone()
            resultados.append((checagem, int(linha["total"]) if linha else 0))
    return resultados


def contagem_fim_a_fim(con_origem, con_destino, origem, cad_origem):
    """Compara origem e destino pelo recorte, sem passar pelo mapa."""
    with con_origem.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS t FROM `timoveis` WHERE ImoCadCodigo = %s", (cad_origem,)
        )
        imoveis_origem = cur.fetchone()["t"]
        cur.execute(
            "SELECT COUNT(*) AS t FROM `tfotosimoveis` f "
            "JOIN `timoveis` i ON i.ImoCodigo = f.FotImoCodigo "
            "WHERE i.ImoCadCodigo = %s",
            (cad_origem,),
        )
        fotos_origem = cur.fetchone()["t"]

    with con_destino.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS t FROM `_mig_mapa_imoveis` "
            "WHERE origem = %s AND imo_cad_origem = %s",
            (origem, cad_origem),
        )
        imoveis_destino = cur.fetchone()["t"]
        cur.execute(
            "SELECT COUNT(*) AS t FROM `_mig_mapa_fotos` mf "
            "JOIN `_mig_mapa_imoveis` mi ON mi.origem = mf.origem "
            "   AND mi.imo_codigo_origem = mf.imo_codigo_origem "
            "WHERE mf.origem = %s AND mi.imo_cad_origem = %s",
            (origem, cad_origem),
        )
        fotos_destino = cur.fetchone()["t"]

    return {
        "imoveis_origem": imoveis_origem,
        "imoveis_migrados": imoveis_destino,
        "fotos_origem_vinculadas": fotos_origem,
        "fotos_migradas": fotos_destino,
    }
