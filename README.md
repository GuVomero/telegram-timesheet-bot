# Telegram Clock In

English version: [README_en.md](README_en.md)

Bot de Telegram para registrar ponto (`entrada`, `almoco`, `entrada_2` e `saida`) em grupo e gerar planilha mensal automaticamente.

## Funcionalidades

- Registro de ponto por usuário no grupo
- Registro em lote por comando com alvo opcional (ex.: `/entrada eu colega`)
- Comandos simples:
  - `/help`
  - `/entrada`
  - `/almoco`
  - `/entrada_2`
  - `/saida`
  - `/status` (último registro do usuário)
  - `/clear [data] [usuarios]` (apaga registros do dia atual ou da data informada)
  - `/corrigir <tipo> <HH:MM> [usuarios]` (adiciona correção manual de hoje)
  - `/corrigir <h1> <h2> <h3> <h4> [usuarios]` (correção em bloco de hoje)
  - `/corrigir <data> <tipo> <HH:MM> [usuarios]` (adiciona correção manual em outro dia)
  - `/corrigir <data> <h1> <h2> <h3> <h4> [usuarios]` (correção em bloco em outro dia)
  - `/mes` (gera planilha do mês anterior sob demanda)
  - `/mes_atual` (gera planilha do mês atual, ainda incompleta)
  - `/mes_png` (gera imagens PNG da tabela do mês anterior, uma por usuário)
  - `/mes_png_atual` (gera imagens PNG do mês atual, uma por usuário)
  - `/chat_id` (mostra o ID do grupo atual)
- Geração automática mensal de planilha `.xlsx`
- Aviso automático às 20:00 caso haja pendências no dia
- Geração manual de imagem `.png` por usuário para compartilhar no grupo
- Banco local SQLite (simples e portátil)

## Requisitos

- Python 3.10+

## Configuração

1. Crie o bot via [@BotFather](https://t.me/BotFather) e copie o token.
2. Adicione o bot no grupo onde vocês vão bater ponto.
3. Pegue o `chat_id` do grupo (valor negativo, normalmente começa com `-100...`).
4. Copie o arquivo de exemplo:

```bash
cp .env.example .env
```

5. Edite `.env`:

```env
BOT_TOKEN=...
TARGET_CHAT_ID=-100...
TIMEZONE=America/Sao_Paulo
# Opcional:
# FIXED_USERS=eu=11111111|Seu Nome;colega=22222222|Nome Colega
```

Se quiser travar o bot para aliases fixos (recomendado no seu caso), configure `FIXED_USERS`.
Assim comandos com argumentos como `eu` e `colega` sempre resolvem para os mesmos IDs.

## Rodando local

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m src.main
```

Ou, via Makefile:

```bash
make setup
make run
```

## Deploy com systemd

Existe um unit file de exemplo em `deploy/systemd/telegram-timesheet-bot.service`.
Ele usa placeholders (`<APP_USER>` e `<APP_DIR>`) para evitar expor dados de infraestrutura.

1. Copie e ajuste os placeholders:

```bash
cp deploy/systemd/telegram-timesheet-bot.service /tmp/telegram-timesheet-bot.service
sed -i "s|<APP_USER>|seu_usuario|g" /tmp/telegram-timesheet-bot.service
sed -i "s|<APP_DIR>|/caminho/da/aplicacao|g" /tmp/telegram-timesheet-bot.service
```

2. Instale e ative no servidor:

```bash
sudo cp /tmp/telegram-timesheet-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable telegram-timesheet-bot
sudo systemctl restart telegram-timesheet-bot
sudo systemctl status telegram-timesheet-bot
```

## Como funciona o fechamento mensal

- O bot agenda execução no dia **1 de cada mês às 08:00** (timezone configurada).
- Ele gera a planilha do **mês anterior** e envia no grupo configurado.
- O bot envia alerta diário às **20:00** se houver pendência de ponto.

## Estrutura

- `src/main.py`: handlers do Telegram, regras de registro e agendamento
- `src/storage.py`: acesso ao SQLite
- `src/report.py`: geração da planilha `.xlsx` e imagens `.png`
- `data/MM_YYYY.db`: bancos mensais criados automaticamente (ex.: `03_2026.db`)
- `reports/`: planilhas geradas
- `.github/workflows/ci.yml`: validação automática no GitHub Actions
- `CONTRIBUTING.md`: guia de contribuição
- `SECURITY.md`: política de segurança e reporte
- `Makefile`: atalhos para setup, execução e checagens

## Publicação Segura no GitHub

- Os padrões abaixo já estão bloqueados no `.gitignore`:
  - `.env` e `.env.*` (com exceção de `.env.example`)
  - `data/`, `reports/`, `*.db`, `*.sqlite`, `*.sqlite3`
  - `venv/`, `.venv/`, caches e arquivos locais de IDE
- Antes do primeiro push, valide:

```bash
make check
git status
```

## Observações

- Se alguém quebrar a sequência (ex.: esquecer `/saida`), o dia fica com pendência na planilha.
- Sequencias adotadas: `entrada -> almoco`, `entrada_2 -> saida` e `entrada -> almoco -> entrada_2 -> saida`.
- Comandos `/entrada`, `/almoco`, `/entrada_2`, `/saida`, `/clear` e `/corrigir` aceitam alvos opcionais por nome conhecido no grupo (ou `eu`).
- O bot valida comandos apenas no `TARGET_CHAT_ID`.
- Os registros agora ficam particionados por mês em arquivos `.db` separados.
- Para produzir planilha manualmente, use `/mes`.
- Para produzir planilha parcial do mês atual, use `/mes_atual`.
- Para produzir imagens de tabela por usuário, use `/mes_png`.
- Para produzir imagens parciais do mês atual por usuário, use `/mes_png_atual`.
- Para apagar registros, use `/clear [YYYY-MM-DD|DD/MM/YYYY] [usuarios]`.
- Exemplo hoje: `/clear eu colega`.
- Exemplo em data especifica: `/clear 02/03/2026 gustavo caio`.
- Para inserir correção manual do dia atual, use `/corrigir <entrada|almoco|entrada_2|saida> <HH:MM> [usuarios]`.
- Para inserir correção em outra data, use `/corrigir <YYYY-MM-DD|DD/MM/YYYY> <entrada|almoco|entrada_2|saida> <HH:MM> [usuarios]`.
- Formato em bloco de horários: `entrada almoco entrada_2 saida` (use `-` para não alterar um slot).
- Exemplo: `/corrigir 02/03/2026 09:00 13:00 14:00 18:30 gustavo caio`.
- Exemplo com parcial: `/corrigir 02/03/2026 09:00 13:00 - - gustavo`.
- Para consultar o ID do grupo já configurado, use `/chat_id`.
