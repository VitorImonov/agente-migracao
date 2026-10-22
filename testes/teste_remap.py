"""Teste offline da logica de remapeamento. Sem banco."""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from migracao.esquema import Tabela
from migracao.migrador import Migrador

falhas = []


def check(nome, ok, detalhe=""):
    print(f"[{'OK  ' if ok else 'FALHA'}] {nome}" + (f" -- {detalhe}" if detalhe else ""))
    if not ok:
        falhas.append(nome)


def col(nome, tipo="int(11)", tamanho=None, extra="", dt="int"):
    return {"COLUMN_NAME": nome, "COLUMN_TYPE": tipo, "IS_NULLABLE": "NO",
            "COLUMN_KEY": "", "EXTRA": extra, "CHARACTER_MAXIMUM_LENGTH": tamanho,
            "DATA_TYPE": dt}


tab_imo = Tabela("timoveis", [
    col("ImoCodigo", extra="auto_increment"),
    col("ImoCadCodigo"),
    col("ImoRef", "varchar(20)", 20, dt="varchar"),
    col("ImoTitulo", "varchar(100)", 100, dt="varchar"),
    col("ImoAngariacao", "date", None, dt="date"),
    col("ImoDtInsert", "date", None, dt="date"),
    col("ImoFotos"),
    col("ImoFotoDestaque"),
    col("ImoFotoUrl", "text", 65535, dt="text"),
    col("ImoCodigoMigracao", "varchar(40)", 40, dt="varchar"),
])
tab_fot = Tabela("tfotosimoveis", [
    col("FotCodigo", extra="auto_increment"),
    col("FotImoCodigo"),
    col("FotUrl", "text", 65535, dt="text"),
    col("FotPosicao"),
])
tabelas = {"imoveis_origem": tab_imo, "imoveis_destino": tab_imo,
           "fotos_origem": tab_fot, "fotos_destino": tab_fot}


class Opcoes:
    cad_origem, cad_destino = 57, 112
    dry_run = limite = desde = None
    bloco, execucao, rotulo_origem = 200, "teste", "banco_nexus"
    checar_ref = carimbar = parar_no_erro = False


class CursorFalso:
    """Registra os UPDATE/INSERT em vez de executar."""
    def __init__(self):
        self.chamadas = []

    def execute(self, sql, args=None):
        self.chamadas.append((" ".join(sql.split()), args))

    def executemany(self, sql, args):
        self.chamadas.append((" ".join(sql.split()), args))


m = Migrador(None, None, tabelas, Opcoes())

# --- 1. SQL de insercao nao deve conter o AUTO_INCREMENT -------------------
check("INSERT de imovel exclui ImoCodigo", "`ImoCodigo`" not in m.sql_imovel)
check("INSERT de imovel inclui ImoCadCodigo", "`ImoCadCodigo`" in m.sql_imovel)
check("INSERT de foto exclui FotCodigo", "`FotCodigo`" not in m.sql_foto)
check("INSERT de foto inclui FotImoCodigo", "`FotImoCodigo`" in m.sql_foto)
check("placeholders batem com colunas",
      m.sql_imovel.count("%s") == len(m.cols_imovel),
      f"{m.sql_imovel.count('%s')} vs {len(m.cols_imovel)}")

# --- 2. ImoCadCodigo trocado pelo do destino ------------------------------
imo = {"ImoCodigo": 4312, "ImoCadCodigo": 57, "ImoRef": "AP-100",
       "ImoTitulo": "Apto centro", "ImoAngariacao": "0000-00-00",
       "ImoDtInsert": "2019-04-02", "ImoFotos": 99, "ImoFotoDestaque": 777,
       "ImoFotoUrl": "antiga.jpg", "ImoCodigoMigracao": ""}
avisos = []
valores = dict(zip(m.cols_imovel, m._valores_imovel(imo, avisos)))
check("ImoCadCodigo gravado e o do DESTINO", valores["ImoCadCodigo"] == 112,
      f"gravou {valores['ImoCadCodigo']}")
check("demais campos vem da origem", valores["ImoTitulo"] == "Apto centro")
check("data zero preservada como string", valores["ImoAngariacao"] == "0000-00-00")

# --- 3. truncamento detectado --------------------------------------------
avisos = []
m._valores_imovel({**imo, "ImoTitulo": "x" * 150}, avisos)
check("truncamento detectado", any("ImoTitulo" in d for _, d in avisos),
      str(avisos))

# --- 4. datas zeradas listadas -------------------------------------------
zeradas = m._datas_zeradas(imo)
check("detecta 0000-00-00 em ImoAngariacao", zeradas == ["ImoAngariacao"], str(zeradas))

# --- 5. FotImoCodigo SEMPRE o novo ---------------------------------------
foto = {"FotCodigo": 555, "FotImoCodigo": 4312, "FotUrl": "a.jpg", "FotPosicao": 1}
vals = dict(zip(m.cols_foto, m._valores_foto(foto, 901877, [])))
check("FotImoCodigo usa o ID NOVO", vals["FotImoCodigo"] == 901877,
      f"gravou {vals['FotImoCodigo']}")
check("FotImoCodigo nunca o antigo", vals["FotImoCodigo"] != 4312)

# --- 6. destaque remapeado pelo mapa -------------------------------------
fotos = [
    {"FotCodigo": 555, "FotUrl": "a.jpg", "FotPosicao": 1},
    {"FotCodigo": 777, "FotUrl": "b.jpg", "FotPosicao": 2},
    {"FotCodigo": 888, "FotUrl": "c.jpg", "FotPosicao": 3},
]
novos = [9001, 9002, 9003]
cur = CursorFalso()
avisos = []
m._atualizar_contadores(cur, imo, 901877, fotos, novos, avisos)
sql, args = cur.chamadas[-1]
check("ImoFotos recalculado", args[0] == 3, f"gravou {args[0]}")
check("ImoFotoDestaque remapeado 777 -> 9002", args[1] == 9002, f"gravou {args[1]}")
check("ImoFotoUrl vem da foto destaque", args[2] == "b.jpg", f"gravou {args[2]}")
check("ImoFotos da origem (99) ignorado", args[0] != 99)

# --- 7. destaque invalido cai na primeira foto ---------------------------
cur = CursorFalso()
avisos = []
m._atualizar_contadores(cur, {**imo, "ImoFotoDestaque": 31337}, 901877, fotos, novos, avisos)
sql, args = cur.chamadas[-1]
check("destaque invalido -> primeira foto", args[1] == 9001, f"gravou {args[1]}")
check("destaque invalido gera aviso", any("destaque" in t for t, _ in avisos), str(avisos))

# --- 8. imovel sem fotos -------------------------------------------------
cur = CursorFalso()
m._atualizar_contadores(cur, imo, 901877, [], [], [])
sqls = " | ".join(s for s, _ in cur.chamadas)
check("sem fotos: ImoFotos=0 e destaque=0", "ImoFotos = 0, ImoFotoDestaque = 0" in sqls, sqls)
check("sem fotos: ImoFotoUrl nao e sobrescrito", "ImoFotoUrl" not in sqls)

# --- 9. carimbo opcional -------------------------------------------------
class OpCarimbo(Opcoes):
    carimbar = True

m2 = Migrador(None, None, tabelas, OpCarimbo())
v2 = dict(zip(m2.cols_imovel, m2._valores_imovel(imo, [])))
check("carimbo grava nexus:<id>", v2["ImoCodigoMigracao"] == "nexus:4312",
      v2["ImoCodigoMigracao"])

# --- 10. cadastro: escolha da chave natural ------------------------------
from migracao.cadastro import COLUNAS_SENSIVEIS, escolher_chave

cad = {"CadUsuario": "imob01", "CadCpfCnpj": "12345678000199",
       "CadEmail": "a@b.com", "CadCreci": "J-1234", "CadNome": "Imobiliaria X"}
check("chave natural prefere CadUsuario", escolher_chave(cad) == ("CadUsuario", "imob01"),
      str(escolher_chave(cad)))

check("chave natural respeita a preferida",
      escolher_chave(cad, "CadCpfCnpj") == ("CadCpfCnpj", "12345678000199"),
      str(escolher_chave(cad, "CadCpfCnpj")))

check("chave natural pula vazio",
      escolher_chave({**cad, "CadUsuario": "   "}) == ("CadCpfCnpj", "12345678000199"))

check("chave natural pula placeholder '0'",
      escolher_chave({"CadUsuario": "0", "CadCpfCnpj": "", "CadEmail": "x@y.com",
                      "CadCreci": ""}) == ("CadEmail", "x@y.com"))

check("sem chave natural devolve (None, None)",
      escolher_chave({"CadUsuario": "", "CadCpfCnpj": "", "CadEmail": "",
                      "CadCreci": ""}) == (None, None))

check("credenciais do cliente marcadas como sensiveis",
      {"CadIpSenha", "CadSenha", "CadToken"} <= COLUNAS_SENSIVEIS)

print()
if falhas:
    print(f"{len(falhas)} FALHA(S): " + ", ".join(falhas))
    raise SystemExit(1)
print("Todos os testes passaram.")
