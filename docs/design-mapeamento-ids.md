# Como a relação imóvel ↔ fotos se mantém coerente

Origem `banco_nexus` → destino `novon14`, estruturas idênticas, recorte por `ImoCadCodigo`.

## O problema

`timoveis.ImoCodigo` é `AUTO_INCREMENT`. O destino já tem linhas e sua própria sequência.
Logo o imóvel `ImoCodigo=4312` da origem vira, por exemplo, `ImoCodigo=901877` no destino.

`tfotosimoveis.FotImoCodigo` guarda o ID antigo. Copiar a foto sem traduzir esse campo
produz uma de duas coisas, as duas silenciosas:

- aponta para **nada** (nenhum imóvel com aquele ID no destino), ou
- aponta para o **imóvel errado** — um imóvel de outro cliente que por acaso tem aquele ID.

A segunda é a perigosa: não gera erro, não gera órfã detectável por `JOIN`, e entrega a
foto da casa de um cliente na ficha do imóvel de outro. Não existe FK para barrar.

## A regra única que resolve

> **Nenhuma foto é inserida com o `FotImoCodigo` lido da origem.
> O valor é sempre o ID novo, obtido do mapa. Foto sem imóvel no mapa não migra.**

Para isso o fluxo precisa de duas coisas: um **mapa de IDs** persistido e um **limite de
transação** que não deixe imóvel e foto se separarem.

## 1. Mapa de IDs

Tabelas de controle, criadas no destino, prefixo `_mig_` para serem óbvias e descartáveis
depois do corte.

```sql
CREATE TABLE `_mig_mapa_imoveis` (
  `origem`             varchar(40) NOT NULL,   -- 'banco_nexus'
  `imo_cad_codigo`     int(11)     NOT NULL,   -- cliente do recorte
  `imo_codigo_origem`  int(11)     NOT NULL,
  `imo_codigo_destino` int(11)     NOT NULL,
  `qtd_fotos`          int(11)     NOT NULL DEFAULT 0,
  `migrado_em`         datetime    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`origem`,`imo_codigo_origem`),
  UNIQUE KEY `uq_destino` (`imo_codigo_destino`),
  KEY `idx_cliente` (`imo_cad_codigo`)
) ENGINE=InnoDB;

CREATE TABLE `_mig_mapa_fotos` (
  `origem`             varchar(40) NOT NULL,
  `fot_codigo_origem`  int(11)     NOT NULL,
  `fot_codigo_destino` int(11)     NOT NULL,
  `imo_codigo_origem`  int(11)     NOT NULL,
  PRIMARY KEY (`origem`,`fot_codigo_origem`),
  UNIQUE KEY `uq_destino` (`fot_codigo_destino`)
) ENGINE=InnoDB;
```

Cada índice está aí por um motivo:

- **`PRIMARY KEY (origem, imo_codigo_origem)`** — é o que dá idempotência. Rodar de novo
  encontra o mapeamento já existente e pula, em vez de duplicar o imóvel. É garantia do
  banco, não do script: mesmo com duas execuções simultâneas, a segunda toma erro de
  chave duplicada em vez de inserir.
- **`UNIQUE KEY uq_destino`** — impede que dois imóveis da origem apontem para a mesma
  linha do destino. Se isso acontecer, é bug no mapa, e o banco acusa na hora.
- **`origem` na chave** — permite migrar mais de uma base para `novon14` no futuro sem
  colisão de IDs antigos.
- **`_mig_mapa_fotos`** — mesma função, para a foto. Sem ele, reexecutar duplica fotos
  mesmo com o imóvel já correto.

### O mapa fica no destino, não em arquivo local

Isso é decisão de correção, não de conveniência. O `INSERT` do imóvel e o `INSERT` no
mapa acontecem **na mesma transação, na mesma conexão**. Então é impossível existir
imóvel inserido sem mapeamento registrado.

Se o mapa fosse um SQLite na sua máquina, haveria uma janela entre o `COMMIT` no MySQL e
a escrita local. Queda de energia nessa janela = imóvel no destino sem registro no mapa =
reexecução duplica. Com o mapa no destino, essa janela não existe.

## 2. A transação por imóvel

O imóvel é a **unidade de trabalho**. Ele e todas as suas fotos entram num único commit:

```
para cada imóvel do recorte (ImoCadCodigo = X):

    BEGIN

    1. já existe em _mig_mapa_imoveis?  -> SKIP (idempotência)

    2. INSERT INTO novon14.timoveis (<todas as colunas menos ImoCodigo>)
       novo_imo = cursor.lastrowid

    3. INSERT INTO _mig_mapa_imoveis (origem, cliente, imo_origem, novo_imo)

    4. SELECT * FROM banco_nexus.tfotosimoveis
         WHERE FotImoCodigo = <imo_origem>
         ORDER BY FotPosicao, FotCodigo

    5. para cada foto: INSERT com FotImoCodigo = novo_imo   <-- ID NOVO, nunca o antigo
       registra cada FotCodigo novo em _mig_mapa_fotos

    6. UPDATE novon14.timoveis SET
            ImoFotos        = <contagem real inserida>,
            ImoFotoDestaque = <FotCodigo NOVO da foto destaque>,
            ImoFotoUrl      = <URL da foto destaque>
          WHERE ImoCodigo = novo_imo

    COMMIT   -- ou ROLLBACK: imóvel e fotos juntos, sempre
```

O que esse desenho garante, por construção:

| Estado impossível | Por quê |
|---|---|
| Foto apontando para imóvel inexistente | `FotImoCodigo` vem do `lastrowid` do passo 2, dentro da mesma transação |
| Foto apontando para imóvel de outro cliente | o ID novo nunca é adivinhado nem reaproveitado |
| Imóvel migrado pela metade (sem fotos) | falha no passo 5 dá `ROLLBACK` e o imóvel também desaparece |
| Imóvel duplicado em reexecução | passo 1 + `PRIMARY KEY` do mapa |
| Foto duplicada em reexecução | `_mig_mapa_fotos` |
| `ImoFotos` mentindo | passo 6 recalcula do que realmente entrou |

Ponto de atenção no passo 6: `ImoFotoDestaque` da origem guarda um `FotCodigo` **antigo**.
Copiar esse número é o mesmo erro do `FotImoCodigo`, só mais discreto. Tem que ser
traduzido pelo `_mig_mapa_fotos`.

InnoDB não devolve números de `AUTO_INCREMENT` em `ROLLBACK`, então vão existir buracos
na sequência do destino. Isso é inofensivo — nenhum dado depende de IDs contíguos.

## 3. Fotos órfãs

Com recorte por `ImoCadCodigo`, há dois tipos de foto que **não** devem migrar:

1. `FotImoCodigo` que não existe em `timoveis` (lixo histórico da origem);
2. `FotImoCodigo` de imóvel que existe mas é de **outro** `ImoCadCodigo` — fora do recorte.

Por isso o passo 4 busca as fotos **a partir do imóvel já migrado**, nunca varrendo
`tfotosimoveis` de forma independente. Uma foto só existe no destino se o pai dela passou
pelo passo 2. As que sobrarem vão para relatório, não para o banco.

Contagem prévia das órfãs (não migra nada, só mede):

```sql
SELECT COUNT(*)
  FROM banco_nexus.tfotosimoveis f
  LEFT JOIN banco_nexus.timoveis i ON i.ImoCodigo = f.FotImoCodigo
 WHERE i.ImoCodigo IS NULL;
```

## 4. Validação depois do corte

Três consultas que têm que voltar zero. Se alguma voltar linha, a migração não está
coerente e não se faz o corte.

```sql
-- A) foto apontando para imóvel que não existe no destino
SELECT COUNT(*) FROM novon14.tfotosimoveis f
  JOIN _mig_mapa_fotos mf ON mf.fot_codigo_destino = f.FotCodigo
  LEFT JOIN novon14.timoveis i ON i.ImoCodigo = f.FotImoCodigo
 WHERE i.ImoCodigo IS NULL;

-- B) foto que trocou de dono: pai no destino != pai mapeado da origem
SELECT COUNT(*) FROM _mig_mapa_fotos mf
  JOIN novon14.tfotosimoveis f ON f.FotCodigo = mf.fot_codigo_destino
  JOIN _mig_mapa_imoveis mi ON mi.imo_codigo_origem = mf.imo_codigo_origem
 WHERE f.FotImoCodigo <> mi.imo_codigo_destino;

-- C) divergencia de contagem por imovel: origem vs destino
SELECT mi.imo_codigo_origem, mi.qtd_fotos AS esperado, COUNT(f.FotCodigo) AS real_
  FROM _mig_mapa_imoveis mi
  LEFT JOIN novon14.tfotosimoveis f ON f.FotImoCodigo = mi.imo_codigo_destino
 GROUP BY mi.imo_codigo_origem, mi.qtd_fotos
HAVING esperado <> real_;
```

A consulta **B** é a que pega o erro silencioso — foto entregue no imóvel errado.
É a mais importante das três.

## 5. Caminho rápido: mesmo servidor MySQL

Se `banco_nexus` e `novon14` estiverem **na mesma instância**, dá para fazer em SQL puro,
sem trafegar 64M de linhas pela rede. O truque é usar `ImoCodigoMigracao` (varchar(40),
já existe no schema) como carimbo do ID de origem, e reconstruir o mapa com `JOIN`:

```sql
-- 1. imoveis, carimbando a origem
INSERT INTO novon14.timoveis (<colunas menos ImoCodigo>, ImoCodigoMigracao)
SELECT <colunas menos ImoCodigo>, CONCAT('nexus:<RUN>:', ImoCodigo)
  FROM banco_nexus.timoveis
 WHERE ImoCadCodigo = <X>;

-- 2. mapa, lendo o carimbo de volta
INSERT INTO _mig_mapa_imoveis (origem, imo_cad_codigo, imo_codigo_origem, imo_codigo_destino)
SELECT 'banco_nexus', ImoCadCodigo,
       CAST(SUBSTRING_INDEX(ImoCodigoMigracao, ':', -1) AS UNSIGNED), ImoCodigo
  FROM novon14.timoveis
 WHERE ImoCodigoMigracao LIKE 'nexus:<RUN>:%';

-- 3. fotos, com o pai resolvido pelo JOIN
INSERT INTO novon14.tfotosimoveis (FotImoCodigo, FotLiberado, FotLegenda, FotLegenda2,
                                   FotPosicao, FotDownload, FotUrl, Fot360)
SELECT m.imo_codigo_destino, f.FotLiberado, f.FotLegenda, f.FotLegenda2,
       f.FotPosicao, f.FotDownload, f.FotUrl, f.Fot360
  FROM banco_nexus.tfotosimoveis f
  JOIN _mig_mapa_imoveis m ON m.imo_codigo_origem = f.FotImoCodigo
 WHERE m.origem = 'banco_nexus' AND m.imo_cad_codigo = <X>;
```

`<RUN>` é um identificador único da execução. Ele importa: sem ele, o `LIKE` do passo 2
pode capturar linhas de uma migração anterior — a origem já tem sinais de ter sido destino
de migração antes (`ImoRefMigracao`, `ImoCodigoMigracao`). Antes de usar essa coluna é
obrigatório conferir que ela está livre no destino:

```sql
SELECT COUNT(*) FROM novon14.timoveis WHERE ImoCodigoMigracao <> '';
```

Trade-off honesto: o passo 3 inteiro é uma transação só. Em volume grande ela fica longa,
segura locks e incha o undo log. Mitigação: fatiar por faixa de `FotCodigo`.
Se as bases estiverem em **servidores diferentes**, esse caminho não existe e vale o
desenho da seção 2.

## Pendências que afetam este desenho

1. **Mesmo servidor?** Decide entre seção 2 (streaming Python) e seção 5 (SQL puro).
2. **`ImoCadCodigo` é o mesmo número nas duas bases?** Se o cliente tem código diferente
   em `novon14`, é preciso um segundo mapa — e o `ImoCadCodigo` inserido não pode ser o
   copiado da origem.
3. **`ImoRef` colide?** É a referência que o corretor usa. Se o destino já tiver um imóvel
   com o mesmo `ImoRef` para aquele cliente, precisa de regra: manter, prefixar ou renumerar.
4. ~~**Demais FKs têm o mesmo problema do `FotImoCodigo`**~~ — **resolvido**:
   `ImoCidCodigo`, `ImoCliCodigo`, `ImoLocCodigo`, `ImoEmpCodigo` são copiados crus de
   propósito. Nenhum deles é usado de forma que o valor da origem gere problema no
   destino, então não há remapeamento a fazer. `ImoCadCodigo` é a única exceção e
   **recebe** o `CadCodigo` novo (seção 6).

---

## 6. O elo do cadastro (tcadastro) e o problema do MyISAM

Acrescentado depois: o cadastro da imobiliaria tambem migra, e o `CadCodigo` novo
passa a ser a fonte do `ImoCadCodigo`. A cadeia tem dois remapeamentos:

```
tcadastro      CadCodigo 57 -> 912          (lastrowid)
timoveis       ImoCadCodigo = 912
               ImoCodigo 4312 -> 901877     (lastrowid)
tfotosimoveis  FotImoCodigo = 901877
```

### Por que o desenho da secao 2 nao serve aqui

`tcadastro` e `ENGINE=MyISAM`. MyISAM nao suporta transacao: o `INSERT` commita no
ato e o `ROLLBACK` e ignorado em silencio. Toda a garantia da secao 2 -- "o INSERT e o
mapa na mesma transacao, logo nao existe janela" -- deixa de valer.

Pior: misturar MyISAM e InnoDB numa mesma transacao nao da erro. O MySQL aceita, so
nao reverte a parte MyISAM. Um `ROLLBACK` que parece ter funcionado deixa o cadastro
gravado.

### A protecao que substitui a transacao: chave natural

Antes de inserir, procura-se no destino um cadastro que ja seja esse cliente:

| Ordem | Coluna | Por que serve |
|---|---|---|
| 1 | `CadUsuario` | e o login; tem `KEY cadusuario`; dois iguais quebram o acesso, logo e unico na pratica |
| 2 | `CadCpfCnpj` | identidade fiscal da imobiliaria |
| 3 | `CadEmail` | fallback |
| 4 | `CadCreci` | fallback |

Fluxo, na ordem:

1. **mapa** -- achou? reusa. Caminho normal da reexecucao.
2. **chave natural no destino** -- achou? reusa e conserta o mapa. Este e o caminho
   que fecha a janela do MyISAM: o cadastro existe, o mapa nao, e a chave natural
   reencontra o cliente em vez de duplicar.
3. **nada achado** -- `INSERT`, pega `lastrowid`, grava o mapa imediatamente.
4. **mapa falhou depois do INSERT** -- denuncia o orfao com o `CadCodigo` na mao e
   para. Nao se migra imovel para cadastro nao mapeado.

Se a chave natural achar **mais de um** cadastro, aborta. Ambiguidade de cliente nao
se resolve por chute, e escolher errado pendura os imoveis na imobiliaria errada.

### Limite de transacao da cadeia

O cadastro vai no seu proprio commit, antes dos imoveis. Nao da para botar cliente +
10 mil imoveis numa transacao so. Entao existe um estado intermediario legitimo:
cadastro migrado, imoveis ainda nao. Isso e seguro porque a reexecucao reencontra o
cliente (passos 1 e 2) e reaproveita o codigo.

### Checagens que o elo novo acrescenta

| Id | O que pega |
|---|---|
| H | imovel migrado apontando para `CadCodigo` que nao existe -- cliente fantasma |
| I | imovel pendurado em cadastro diferente do mapeado -- a versao "um nivel acima" da checagem B |
| J | cadastro mapeado que nao existe mais no destino |

### Verificacao de engine antes de rodar

Se `timoveis` ou `tfotosimoveis` nao forem InnoDB **no destino**, a atomicidade da
secao 2 nao existe e o script se recusa a rodar. Seguir exige
`--aceitar-sem-transacao`, explicitamente -- porque o modo de falha e silencioso:
imovel gravado sem fotos, sem erro nenhum.

### Credenciais dentro da tcadastro

`CadIpBanco`, `CadIpUsuario`, `CadIpSenha`, `CadIpBase` sao a conexao do banco **do
cliente**. Copiadas cruas, o cadastro migrado aponta para o servidor antigo -- o
sistema novo poderia ler e gravar na base velha. Padrao: copia e avisa. Alternativa:
`--limpar-conexao-banco`.

`CadUsuario`, `CadSenha`, `CadToken`, `CadRecap` nunca aparecem em log ou censo.
