# Análise da origem — `banco_nexus`

Documento de trabalho. Registra o que o DDL já prova e o que só os dados podem responder.
DDL em [schema/origem/banco_nexus/](../schema/origem/banco_nexus/).

## Tabelas

| Tabela | PK | AUTO_INCREMENT | Papel |
|---|---|---|---|
| `timoveis` | `ImoCodigo` | 758.844 | Imóvel. ~270 colunas. |
| `tfotosimoveis` | `FotCodigo` | 64.730.498 | Fotos, N por imóvel. |

Relacionamento: `tfotosimoveis.FotImoCodigo` → `timoveis.ImoCodigo`.
**Não existe FOREIGN KEY** — apenas `KEY fotoimovel`. Nada no banco impede foto órfã.

## Riscos confirmados pelo DDL

### 1. Charset misturado dentro da mesma tabela

Tabela é `DEFAULT CHARSET=latin1 COLLATE=latin1_general_ci`, mas 3 colunas são `utf8mb4`:

- `ImoDescription` varchar(200) utf8mb4
- `ImoDescricao` text utf8mb4
- `ImoDescricaoTemp` text utf8mb4

Todo o resto (`ImoTitulo`, `ImoBairro`, `ImoEndereco`, `ImoObs`, `ImoDescricao2`,
`ImoLocalizacao`, legendas de foto…) é latin1.

O risco real não é o latin1 em si — é **double-encoding**: aplicação PHP gravando bytes
UTF-8 dentro de coluna latin1. Nesse caso o MySQL devolve "JardÃ­m" em vez de "Jardím",
e uma conversão ingênua para utf8mb4 congela o defeito. Precisa de censo nos dados
antes de decidir a conversão — não dá para resolver no papel.

### 2. Números guardados como texto

Valores monetários e áreas são `varchar`:

`ImoValor`, `ImoValorLoc`, `ImoValorTemp`, `ImoValorFic`, `ImoValorLocFic`,
`ImoValorLocTemp`, `ImoValorHe`, `ImoValorAl`, `ImoValorSoja`, `ImoDesconto`, `ImoFci`,
`ImoIptu`, `ImoTaxaLixo`, `ImoTaxaDesastre`, `ImoCondominio`, `ImoAreaTotal`,
`ImoAreaUtil`, `ImoAreaConstruida`, `ImoAreaPrivativa`, `ImoAreaInterna`, `ImoAreaComum`,
`ImoAreaGaragem`, `ImoAreaTerreno`, `ImoAreaAverbada`, `ImoAlqueires`, `ImoHectares`,
`CarValor`.

`ImoValorHe` e `ImoValorAl` têm só varchar(10) — truncamento provável em valor alto.
Formatos esperados no mesmo campo: `350000`, `350.000,00`, `R$ 350.000,00`, `350000.00`,
vazio, `0`, e texto livre (`"a combinar"`). Precisa de regra de parsing + relatório do
que não converter.

### 3. Datas

| Coluna | Tipo | Problema |
|---|---|---|
| `ImoAngariacao` | `date NOT NULL` | sem default → `0000-00-00` quase certo |
| `ImoFimExibicao` | `date NOT NULL` | idem |
| `ImoDtInsert` | `date NOT NULL` | idem |
| `ImoDtEnviaOlx` | `date NOT NULL` | idem |
| `ImoDtAtualizaOlx` | `date NOT NULL` | idem |
| `ImoAtualizacao` | `date NOT NULL DEFAULT '2015-09-09'` | default fixo e antigo |
| `ImoDtAngaria` | `varchar(10)` | **data como texto**, duplica `ImoAngariacao` |
| `ImoDtDel` | `date NULL` | soft delete |
| `ImoDtVenda` | `date NULL` | — |

`0000-00-00` não é data válida: o driver devolve `None` ou estoura, e o destino em
`sql_mode` estrito rejeita. Tem que ser tratado explicitamente na leitura.

Pendência: `ImoAngariacao` (date) e `ImoDtAngaria` (varchar) deveriam ser a mesma
informação. Qual das duas é a confiável?

### 4. Booleanos em quatro convenções diferentes

- `varchar(1)`: `ImoLiberado`, `ImoDestaque`, `FotLiberado`
- `varchar(3)`: `ImoMobiliado`, `ImoSemiMobiliado`, `ImoCozinhaPlanejada`, `ImoEsquina`, `ImoCosinha`, `ImoOferta`
- `varchar(10)`/`varchar(30)`: `ImoPiscina`, `ImoMurado`, `ImoGrade`, `ImoChurrasqueira`, `ImoSacada`, `ImoGesso`, `ImoSauna`, `ImoAcademia`, `ImoElevador` e ~30 outras
- `int(11)` / `tinyint(1)`: `ImoVendido`, `ImoAlugado`, `ImoCasaMista`, `ImoCopa`, `ImoDuplex`, portais

Os valores reais (`S`/`N`, `Sim`/`Não`, `1`/`0`, `X`, vazio) só os dados dizem.
Censo de valores distintos é obrigatório antes de mapear.

### 5. A tabela atende mais de um domínio

Três blocos de colunas que só valem para certos tipos de registro:

- **Veículos** (10 colunas): `CarPlaca`, `CarModelo`, `CarAno`, `CarMarca`, `CarValor`…
- **Rural** (24 colunas): `ImoRural*`
- **Loteamento**: `ImoLoteamento`, `ImoQuadra`, `ImoLote`, `ImoLotQuadra`, `ImoPosicaoLote`

Quem decide o bloco válido é `ImoCat1` / `ImoCat3` / `ImoSubCat` — e o significado
desses códigos não está no DDL.

### 6. Contadores desnormalizados que não podem ser copiados

- `ImoFotos` int — contagem de fotos
- `ImoFotoDestaque` int — provável `FotCodigo` da foto principal
- `ImoFotoUrl` text — URL da foto principal, repetida da `tfotosimoveis`

Se copiados como estão, ficam inconsistentes com o que realmente migrou de
`tfotosimoveis`. Têm que ser **recalculados no destino** depois das fotos.

### 7. Fotos são URL, não binário

`FotUrl` e `Fot360` são `text` — caminho/URL, não blob. Então a migração move string,
não arquivo. Consequência: se o destino tem domínio/storage diferente, as URLs
apontam para o servidor antigo. Decisão pendente (ver abaixo).

Outros pontos da `tfotosimoveis`:
- `FotPosicao` default `99` — ordenação com empate garantido em massa
- `FotLegenda` e `FotLegenda2` em latin1, mesmo risco de encoding
- sem FK → órfãs certas

### 8. Marcas de migração anterior

`ImoRefMigracao` varchar(200) e `ImoCodigoMigracao` varchar(40) indicam que esta base
**já foi destino de uma migração**. Precisa saber se ainda são usados: são os melhores
candidatos a chave de idempotência.

### 9. Volume

`timoveis` AUTO_INCREMENT 758k, `tfotosimoveis` 64,7M. Esses números são de sequência,
não de linhas vivas — a contagem real pode ser bem menor. Mas o cenário tem que
assumir lote + retomada: nada de `SELECT *` inteiro na memória.

## Perguntas abertas

1. **Destino**: qual banco/estrutura? É schema novo, ou base existente com dados?
2. **URLs de foto**: o storage antigo continua no ar? Reescrever URL, copiar arquivo, ou manter?
3. **Escopo**: migra tudo, ou só ativo? Qual coluna manda — `ImoDtDel`, `ImoStatus`, `ImoLiberado`, `ImoVendido`/`ImoAlugado`, `ImoTeste`, `ImoFimExibicao`?
4. **Chave de identidade**: o que diz que um imóvel do destino é o mesmo da origem? `ImoCodigo`, `ImoRef`, `ImoChave`, `ImoCodigoMigracao`?
5. **Códigos**: o que significam `ImoCat1`, `ImoCat3`, `ImoSubCat`, `ImoSituacao`, `ImoStatus`? Existe tabela de domínio?
6. ~~**FK satélites**~~ — **respondido**: `ImoCidCodigo`, `ImoCliCodigo`, `ImoLocCodigo`, `ImoEmpCodigo` são copiados crus, sem remapeamento; nenhum é usado de forma que o valor antigo cause problema no destino. `ImoCadCodigo` recebe o `CadCodigo` novo do cadastro migrado.
