# Claude Música

Aperta o fone, fala o que quer, e o Claude faz no seu Spotify.

```
Fone (aperto longo) → "E aí Siri, Claude música" → você fala o pedido
   → Atalho do iPhone → seu servidor na Vercel → Claude decide o que fazer
   → API do Spotify executa → Siri fala a resposta
```

## O que dá pra pedir

* Tocar: "toca Nightcall", "toca aquela do final de Drive", "toca o álbum novo da Rosalía", "toca minha playlist de academia"
* Controle: "pausa", "pula", "volta", "volta pro começo", "liga o aleatório", "repete essa"
* Fila: "põe na fila as três mais famosas do Radiohead", "o que vem depois?"
* Playlists: "cria uma playlist pra estudar com 20 músicas tipo Bonobo", "adiciona essa na playlist X", "tira essa da playlist X", "renomeia a playlist X"
* Biblioteca: "curte essa", "descurte essa", "segue esse artista", "salva esse álbum"
* Informação: "que música é essa?", "quais foram minhas mais ouvidas esse mês?", "o que eu ouvi agora há pouco?"
* Dispositivos: "passa pro computador"
* Combinações: "cria uma playlist com as minhas 30 mais ouvidas do ano e toca no aleatório"

**Limites da própria API do Spotify** (não tem como contornar): não dá pra remover nem reordenar itens da fila, e as recomendações automáticas do Spotify foram desligadas pra apps novos. Nesse caso o próprio Claude escolhe as músicas parecidas e busca uma por uma.

## O que você precisa

* Spotify Premium (obrigatório desde 2026 pra apps em modo de desenvolvedor)
* Conta no [console da Anthropic](https://console.anthropic.com) com créditos (US$ 5 dão pra milhares de pedidos)
* Conta grátis na [Vercel](https://vercel.com), entrando com o seu GitHub
* Python instalado no computador (só pro passo 2)

---

## Passo 1: criar o app no Spotify (5 min)

1. Entre em [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) com a sua conta Premium.
2. Clique em **Create app**.
   * App name: `Claude Musica`
   * Redirect URI: `http://127.0.0.1:8888/callback` (exatamente assim, com 127.0.0.1 e não localhost)
   * Marque **Web API**.
3. Salve, entre em **Settings** e copie o **Client ID** e o **Client Secret**.

## Passo 2: pegar o refresh token (5 min)

No terminal, dentro da pasta do projeto:

```bash
pip install requests
python pegar_refresh_token.py SEU_CLIENT_ID SEU_CLIENT_SECRET
```

O navegador abre, você autoriza, e o terminal imprime o **refresh token**. Guarde, ele funciona como uma senha.

## Passo 3: criar a chave da API do Claude (3 min)

No [console da Anthropic](https://console.anthropic.com), vá em **API Keys** e crie uma chave. Coloque créditos em **Billing**.

## Passo 4: colocar o servidor no ar (10 min)

1. Suba esta pasta pra um repositório **privado** no seu GitHub.
2. Na Vercel: **Add New → Project**, importe o repositório.
3. Antes de clicar em Deploy, abra **Environment Variables** e crie:

| Nome | Valor |
|---|---|
| `ANTHROPIC_API_KEY` | chave do passo 3 |
| `SPOTIFY_CLIENT_ID` | do passo 1 |
| `SPOTIFY_CLIENT_SECRET` | do passo 1 |
| `SPOTIFY_REFRESH_TOKEN` | do passo 2 |
| `APP_SECRET` | uma senha longa que você inventa (ex. gere em um gerador de senhas) |
| `CLAUDE_MODEL` | opcional; o padrão é `claude-haiku-5-5`, que é o mais rápido. Troque para `claude-sonnet-5-5` se quiser escolhas musicais mais refinadas, com um pouco mais de demora |

4. Deploy. Abra `https://SEU-PROJETO.vercel.app/` no navegador: deve aparecer `{"status": "ok"}`.

**Teste pelo terminal** (com o Spotify tocando em algum aparelho):

```bash
curl -X POST https://SEU-PROJETO.vercel.app/api/comando \
  -H "Authorization: Bearer SUA_APP_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"pedido": "toca Nightcall do Kavinsky"}'
```

## Passo 5: criar o atalho no iPhone (5 min)

Abra o app **Atalhos**, toque em **+** e adicione estas ações, nesta ordem:

1. **Ditar Texto**
   * Idioma: Português (Brasil)
   * Parar de ouvir: Após Pausa
2. **Obter Conteúdo do URL**
   * URL: `https://SEU-PROJETO.vercel.app/api/comando`
   * Toque em "Mostrar Mais":
     * Método: **POST**
     * Cabeçalhos: `Authorization` = `Bearer SUA_APP_SECRET`
     * Corpo da Requisição: **JSON**, com um campo de texto `pedido` cujo valor é a variável **Texto Ditado**
3. **Obter Valor do Dicionário**
   * Chave: `resposta` (no dicionário "Conteúdo do URL")
4. **Falar Texto**, usando o "Valor do Dicionário"

Opcional: coloque uma ação **Abrir App → Spotify** logo depois do Ditar Texto, pra garantir que o iPhone esteja disponível pro Spotify quando o pedido chegar.

Dê o nome **Claude música** ao atalho. Esse nome é a frase que você vai dizer pra Siri.

## Como usar

Segure o botão do fone (ou o sensor da haste, nos AirPods) → "**Claude música**" → espere o som do ditado → fale o pedido.

Dica: em Ajustes → Siri, ative "Permitir Siri quando bloqueado", pra funcionar com o celular no bolso.

---

## Problemas comuns

* **A música toca no computador em vez do celular**: o servidor usa o iPhone como aparelho padrão e só toca em outro se você pedir ("toca no computador", "passa pra TV"). Se o iPhone não aparecer pro Spotify, abra o app do Spotify nele uma vez.
* **Login do Spotify expira**: apps em modo de desenvolvedor recebem um refresh token que vale 180 dias. Depois disso, rode o passo 2 de novo e troque a variável `SPOTIFY_REFRESH_TOKEN` na Vercel.
* **"Nenhum dispositivo ativo"**: o Spotify do iPhone dorme depois de um tempo parado. Abra o app uma vez e tente de novo. Se acontecer muito, coloque uma ação **Abrir App → Spotify** no começo do atalho (o problema é que ela exige o celular desbloqueado).
* **Volume não muda**: o iPhone não deixa apps externos controlarem o volume dele. Funciona no computador e em caixas de som.
* **Erro 403 do Spotify**: confira se a conta que você autorizou no passo 2 é a mesma conta Premium dona do app.
* **Demora**: pedidos simples levam de 2 a 4 segundos; pedidos com muitas músicas (playlist de 30 faixas) podem levar uns 15.
* **Siri entende errado o nome do atalho**: troque por um nome mais fácil de pronunciar, tipo "Música Claude" ou "DJ".

## Arquivos

* `api/index.py`: o servidor (Claude + 30 ferramentas do Spotify)
* `pegar_refresh_token.py`: login único no Spotify
* `vercel.json`, `requirements.txt`: configuração do deploy
