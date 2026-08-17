# Como publicar o app com link (leitura automática por IA, gratuito)

Isso te dá um **link privado** (não listado no Google, só quem tem o link acessa,
e ainda pede senha) onde você sobe o PDF e a IA já lê, decide o modelo e sugere
os campos — você só revisa e clica em gerar.

Vamos em 4 partes. Nenhuma delas envolve pagamento.

---

## Parte 1 — Criar a chave de IA gratuita (Google AI Studio)

1. Acesse **https://aistudio.google.com/app/apikey** e faça login com uma conta Google (Gmail).
2. Clique em **"Create API key"** (ou "Criar chave de API").
3. Copie a chave gerada (uma sequência de letras/números) e guarde num lugar seguro
   — vamos usar ela na Parte 3.

> Essa é a camada gratuita do Gemini. Tem um limite de uso por dia, mas para o volume
> de processos de uma vara, costuma ser suficiente. Se um dia bater no limite, o app
> simplesmente mostra um erro e você tenta de novo mais tarde (sem cobrança).

---

## Parte 2 — Colocar o código no GitHub

1. Acesse **https://github.com** e crie uma conta gratuita (se ainda não tiver).
2. Clique no **+** no canto superior direito → **"New repository"**.
3. Dê um nome, por exemplo `minutar-sentenca` → marque como **Private** (privado,
   importante) → clique em **"Create repository"**.
4. Na página do repositório recém-criado, clique em **"uploading an existing file"**
   (ou "Add file" → "Upload files").
5. Arraste **todos os arquivos e pastas** que estão dentro da pasta `minutar_lote_cloud`
   que te enviei (o .zip) — incluindo as pastas `modelos_usuario`, `templates`, `engine`,
   e os arquivos `app_gemini.py`, `requirements.txt`, `.gitignore`.
   **Não suba o arquivo `.streamlit/secrets.toml.example` como `secrets.toml`** —
   ele é só um exemplo, a chave de verdade vai direto no Streamlit Cloud (Parte 3).
6. Clique em **"Commit changes"** para salvar.

---

## Parte 3 — Publicar no Streamlit Community Cloud (o link)

1. Acesse **https://streamlit.io/cloud** e clique em **"Sign up"**, entrando com a
   sua conta do GitHub (mesma da Parte 2).
2. Clique em **"Create app"** → **"From existing repo"**.
3. Escolha o repositório `minutar-sentenca` que você criou.
4. Em "Main file path", digite: `app_gemini.py`
5. Antes de publicar, clique em **"Advanced settings"** → na caixa **"Secrets"**, cole:
   ```
   APP_PASSWORD = "escolha-uma-senha-forte-aqui"
   GEMINI_API_KEY = "cole-aqui-a-chave-que-voce-copiou-na-parte-1"
   ```
6. Clique em **"Deploy"**. Espere alguns minutos — o Streamlit Cloud instala tudo
   sozinho.
7. Quando terminar, você recebe um link tipo `https://minutar-sentenca-xxxx.streamlit.app`
   — esse é o link que você vai usar (e pode salvar nos favoritos do navegador).

---

## Parte 4 — Usando o app publicado

1. Abra o link, digite a senha que você definiu.
2. Suba o PDF do processo → clique em **"Ler e analisar com IA"**.
3. Aguarde (PDFs grandes podem levar até 1 minuto).
4. Confira o resumo e os campos sugeridos — **edite qualquer coisa que estiver
   errada ou incompleta** antes de gerar.
5. Dê um nome ao caso e clique em **"Gerar minuta"** → baixe o `.docx`.

---

## Se quiser atualizar o código depois

Sempre que eu (ou você) melhorar algum modelo ou o comportamento do app, é só:
1. Editar o arquivo direto na página do GitHub (ou subir de novo via "Upload files").
2. O Streamlit Cloud atualiza o app publicado sozinho, em 1-2 minutos, sem precisar
   fazer nada na Parte 3 de novo.

---

## Segurança e privacidade — leia antes de usar com processos reais

- O repositório no GitHub deve ficar **Private** (Parte 2, passo 3) — não deixe público.
- A senha do app (`APP_PASSWORD`) deve ser compartilhada só com quem precisa usar.
- Os PDFs enviados passam pelo Google (Gemini) para leitura — isso significa que o
  conteúdo do processo (inclusive dados de crianças, saúde, renda) trafega para os
  servidores do Google durante a análise. Se o seu processo correr em segredo de
  justiça ou houver alguma restrição institucional quanto a isso, vale confirmar
  com a área de TI/compliance do tribunal antes de usar este fluxo com processos
  reais.
- Sempre revise a minuta gerada antes de assinar — a IA pode errar, especialmente
  em casos com nuances jurídicas (como vimos no caso da Ísis, que precisou de
  minuta customizada).
