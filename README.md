# Migração `banco_nexus` → `novon14`

Migra uma imobiliária inteira — cadastro, imóveis e fotos — entre dois servidores
MySQL distintos. Roda **local**, sem servidor e sem Docker.

Você informa **só o código de origem**. O código novo nasce do próprio `INSERT`:

```
tcadastro      CadCodigo 57 -> 912          (lastrowid)
timoveis       ImoCadCodigo = 912
               ImoCodigo 4312 -> 901877     (lastrowid)
tfotosimoveis  FotImoCodigo = 901877
```

## Instalação

```bash
pip install -r requirements.txt
cp .env.example .env     # preencha as credenciais das duas pontas
```

O `.env` está no `.gitignore` e nunca deve ser commitado.

## Procedimento

```bash
# 1. CENSO -- somente leitura, não altera nada.
#    Responde a pergunta mais importante: esse cliente já existe no destino?
python censo.py --cad-origem 57

# 2. ENSAIO -- imóveis com ROLLBACK. O cadastro NÃO é inserido (ver MyISAM abaixo).
python migrar.py --cad-origem 57 --dry-run

# 3. SÓ O CADASTRO -- cria a imobiliária e para, para você conferir no sistema.
python migrar.py --cad-origem 57 --so-cadastro

# 4. AMOSTRA -- 20 imóveis para inspeção visual.
python migrar.py --cad-origem 57 --limite 20

# 5. COMPLETA -- reexecutável: o que já entrou é pulado.
python migrar.py --cad-origem 57

# 6. VALIDAÇÃO -- porteiro antes do corte. Sai 1 se algo crítico falhar.
python validar.py --cad-origem 57
```

## O problema do MyISAM

`timoveis` e `tfotosimoveis` são **InnoDB**. `tcadastro` é **MyISAM** — e MyISAM não
tem transação: o `INSERT` commita no ato e `ROLLBACK` não desfaz.

Isso tem três consequências práticas:

**1. O cadastro não pode ser protegido por transação.** A proteção usada no lugar é
**chave natural**: antes de inserir, o script procura no destino um cadastro que já
seja esse cliente (`CadUsuario`, que é o login e tem índice; ou `CadCpfCnpj`, `CadEmail`,
`CadCreci`). Se achar, reusa o `CadCodigo` em vez de criar outro. Isso torna a operação
idempotente sem depender de transação nem do mapa.

Se achar **mais de um**, o script aborta — ambiguidade de cliente não se resolve por
chute.

**2. `--dry-run` não insere o cadastro.** Não daria para desfazer. O ensaio usa um
`CadCodigo` fictício (`-1`) só para exercitar os `INSERT` dos imóveis, que são depois
revertidos. Então o dry-run valida tipos, truncamento e charset dos imóveis, mas não
prova o `INSERT` do cadastro.

**3. Existe uma janela de falha de alguns milissegundos** entre o `INSERT` do cadastro e
o registro no mapa. Se o processo morrer exatamente ali, o script **não** tenta
adivinhar: ele para e te entrega o `CadCodigo` órfão com as duas opções de conserto
(inserir o mapeamento à mão, ou apagar o cadastro). Rodar de novo sem resolver criaria a
imobiliária duplicada — e aí todos os imóveis da segunda rodada iriam para um cliente
fantasma.

O script também **confere a engine no destino** e se recusa a rodar se `timoveis` ou
`tfotosimoveis` não forem InnoDB lá: sem InnoDB, o `ROLLBACK` é ignorado em silêncio e a
garantia de "imóvel e fotos juntos" deixa de existir. Para seguir mesmo assim é preciso
`--aceitar-sem-transacao`, explicitamente.

## Como a relação imóvel ↔ foto se mantém coerente

O `ImoCodigo` muda no destino, então `FotImoCodigo` não pode ser copiado. A regra:

> Nenhuma foto é inserida com o `FotImoCodigo` lido da origem. O valor vem sempre do
> `lastrowid` do `INSERT` do imóvel, na mesma transação.

Cada imóvel é **uma transação**: imóvel + todas as suas fotos + o mapa commitam juntos
ou somem juntos. Isso torna impossível uma foto apontar para imóvel inexistente ou —
o erro silencioso e perigoso — para o imóvel de outro cliente que por acaso tenha o ID
antigo no destino.

Detalhe completo em [docs/design-mapeamento-ids.md](docs/design-mapeamento-ids.md).

## O que é recalculado, não copiado

| Campo | Por quê |
|---|---|
| `ImoCadCodigo` | recebe o `CadCodigo` novo, gerado pelo insert do cadastro |
| `FotImoCodigo` | o `ImoCodigo` muda no destino |
| `ImoFotoDestaque` | guarda um `FotCodigo`, que também muda |
| `ImoFotos` | contagem real do que entrou (o censo mostra que já mente na origem) |
| `ImoFotoUrl` | derivado da foto destaque de verdade |

## Credenciais dentro da `tcadastro`

`CadIpBanco`, `CadIpUsuario`, `CadIpSenha` e `CadIpBase` são as credenciais de conexão do
banco **do cliente**. Copiadas cruas, o cadastro migrado em `novon14` continua apontando
para o **servidor antigo** — o sistema novo poderia acabar lendo e gravando na base
velha.

O comportamento padrão é copiar (cópia fiel) e **avisar em voz alta**, registrando a
ocorrência. Para gravar vazio e preencher depois: `--limpar-conexao-banco`.

`CadUsuario`, `CadSenha`, `CadToken` e `CadRecap` estão marcadas como sensíveis e nunca
são impressas em log, censo ou relatório.

## Tabelas de controle

Criadas no destino, prefixo `_mig_`, descartáveis depois do corte:

- **`_mig_mapa_cadastro`** — `CadCodigo` origem → destino. É o que impede a imobiliária
  de ser duplicada. InnoDB, mesmo com `tcadastro` em MyISAM.
- **`_mig_mapa_imoveis`** — `ImoCodigo` origem → destino. A `PRIMARY KEY
  (origem, imo_codigo_origem)` é o que dá idempotência: reexecutar não duplica porque a
  segunda tentativa bate em chave duplicada. É garantia do banco, não do script.
- **`_mig_mapa_fotos`** — mesmo papel para `FotCodigo`.
- **`_mig_ocorrencia`** — o que chamou atenção (data zerada, `ImoRef` duplicada, destaque
  não resolvido, truncamento, cadastro reusado, credenciais copiadas, falha).

Depois do corte e da validação aprovada:

```python
from migracao import config, controle
from migracao.db import conectar
config.carregar_env()
controle.limpar(conectar(config.parametros("DESTINO"), autocommit=True))
```

## Decisões técnicas que podem surpreender

**Datas vêm como string.** A origem tem colunas `date NOT NULL` sem default, o que quase
garante `0000-00-00`. O conversor padrão do PyMySQL devolve `None` para essa data, e
`None` num `date NOT NULL` quebra o `INSERT`. Mantendo string, o valor entra no destino
exatamente como estava. Cópia fiel, sem inventar data substituta.

**`sql_mode` da sessão é `ALLOW_INVALID_DATES`.** Necessário para o item acima. O efeito
colateral é perder o erro de truncamento, que ficaria silencioso — por isso o tamanho é
conferido em Python antes do `INSERT`, e cada estouro vira ocorrência.

**Encoding não é "corrigido".** A tabela é latin1 com três colunas utf8mb4. Se o texto
estiver double-encoded na origem (PHP gravando UTF-8 em coluna latin1), ele chega
double-encoded no destino. A ida e volta é byte-exata, não piora nada — mas também não
conserta. O censo mede quantos registros têm esse sinal; corrigir é decisão à parte.

**`--dry-run` consome `AUTO_INCREMENT`.** Os `INSERT`s dos imóveis acontecem de verdade
antes do `ROLLBACK`, e o InnoDB não devolve os números. Vão existir buracos na sequência.
Inofensivo: nada depende de IDs contíguos.

## Testes

```bash
python testes/teste_remap.py
```

27 checagens, sem banco: `FotImoCodigo` sempre recebe o ID novo, `ImoFotoDestaque` é
remapeado pelo mapa, `ImoCadCodigo` é o do destino, contadores são recalculados, data
zero sobrevive, truncamento é detectado, chave natural do cadastro escolhe certo.

## Fora de escopo (combinado)

As outras FKs — `ImoCidCodigo`, `ImoCliCodigo`, `ImoLocCodigo`, `ImoEmpCodigo`,
`CadCidCodigo`, `CadCatCodigo` — são copiadas **cruas**, e isso está correto: nenhuma
delas é usada de forma que o valor antigo cause problema no destino. Não precisam de
remapeamento — ao contrário do `FotImoCodigo`, que precisa. O que veio da origem
permanece como veio.

Também fora: as URLs em `FotUrl`, `CadFoto1..3` e `ImoFotoUrl` apontam para o storage do
servidor antigo. A migração copia a string como está. Note que `CadServerFotos` indica
qual servidor de fotos o cliente usa — provavelmente relevante quando essa parte entrar.
