# Servidor de registros

Cloudflare Worker (`migrador-whatsapp-logs`, conta DAGUMASI) com banco D1 `migrador-whatsapp-logs`. Ele recebe:

- **eventos anônimos** de quem aceitou enviar (`POST /v1/eventos`, JSON em lotes de até 50);
- **pacotes de diagnóstico** enviados pelo botão do programa (`POST /v1/diagnosticos`, um `.zip` de até 1,8 MB que o programa já anonimizou). A resposta traz o protocolo (ex.: `BEKA-H2TJ`) que aparece para a pessoa.

Endereço: `https://migrador-whatsapp-logs.dagumasi.workers.dev`

## Proteções

- Limite por origem (IP guardado só como hash) e por hora: 120 envios de eventos e 6 diagnósticos.
- Tamanho máximo por envio, validação do tipo de evento e do `.zip`.
- Rotas `/admin/*` exigem `Authorization: Bearer <ADMIN_TOKEN>` (segredo do Worker).
- Um cron diário apaga eventos e diagnósticos com mais de 180 dias.

## Consultas

```bash
TOKEN=$(cat ~/.config/migrador-whatsapp/admin-token.txt)
U=https://migrador-whatsapp-logs.dagumasi.workers.dev

curl -H "Authorization: Bearer $TOKEN" "$U/admin/resumo?dias=30"            # visão geral e falhas recentes
curl -H "Authorization: Bearer $TOKEN" "$U/admin/eventos?tipo=backup_falha"   # filtros: tipo, instalacao, sessao, versao, dias, simulacao=1
curl -H "Authorization: Bearer $TOKEN" "$U/admin/diagnosticos"               # lista de protocolos
curl -H "Authorization: Bearer $TOKEN" -o diag.zip "$U/admin/diagnosticos/BEKA-H2TJ"
```

Ou direto no banco: `npx wrangler d1 execute migrador-whatsapp-logs --remote --command "SELECT ..."`.

Para seguir uma migração do começo ao fim, filtre por `sessao`. Para ver todas as tentativas de um mesmo PC, filtre por `instalacao`.

## Eventos

| Tipo | Quando |
|---|---|
| `programa_aberto` | Programa aberto |
| `sistema` | Verificação do PC (preparação, conexão, backup, restauração) |
| `conferencia`, `conferencia_destino` | Conferência dos aparelhos e do iPhone novo, com pendências |
| `senha_ok`, `senha_falha` | Senha do backup |
| `backup_inicio`, `backup_ok`, `backup_falha`, `backup_cancelado` | Backup, com duração e % atingida |
| `verificacao`, `verificacao_falha` | Resultado da verificação (contagens, anexos, amostra) |
| `restauracao_inicio`, `restauracao_ok`, `restauracao_falha` | Restauração |
| `resultado`, `resultado_confirmado` | WhatsApp no iPhone novo e a confirmação da pessoa |
| `excecao` | Erro não tratado (tipo, mensagem anonimizada, arquivo:linha) |

## Publicar mudanças

```bash
cd servidor
npx wrangler deploy                                        # código
npx wrangler d1 migrations apply migrador-whatsapp-logs --remote   # nova migração em migrations/
npx wrangler secret put ADMIN_TOKEN                        # trocar o token
```
