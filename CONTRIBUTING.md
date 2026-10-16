# Como contribuir

O Migrador de WhatsApp para iPhone é um projeto de código aberto da **DAGUMASI Sistemas**, aberto à comunidade. Toda ajuda é bem-vinda: relatar problemas, testar com aparelhos diferentes, melhorar textos, traduzir e enviar código.

## Relatar um problema

Abra uma [issue](https://github.com/olegantonov/whatsapp-iphone-migrador/issues/new/choose) com:

- a versão do programa (aparece no canto inferior esquerdo);
- os modelos e as versões do iOS dos dois iPhones;
- o que você fez, o que esperava e o que aconteceu;
- o arquivo de registro: botão **Abrir registros** no programa, arquivo mais recente.

**Antes de anexar o registro, confira o conteúdo.** Ele não tem senhas nem conversas, mas mostra nomes dos aparelhos e caminhos de pastas do seu PC.

## Testar

Relatos de uso com iPhones reais valem muito, principalmente com combinações de modelos e versões do iOS diferentes das já testadas. Conte como foi, inclusive quando deu tudo certo.

## Enviar código

```powershell
git clone https://github.com/olegantonov/whatsapp-iphone-migrador
cd whatsapp-iphone-migrador
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest                  # testes
.venv\Scripts\python main.py --simulacao        # interface sem iPhones
```

1. Crie uma branch a partir da `main`.
2. Mantenha o estilo do código: nomes e mensagens em português, textos da interface claros para quem não é técnico.
3. Toda mudança nas regras de segurança (`checagens.py`, `verificacao.py`, `motor.py`) precisa de teste em `tests/`. Se a mudança envolve uma situação de erro, considere também um cenário novo em `migrador/dispositivos/simulado.py`.
4. Abra um pull request explicando o que muda e como foi testado. O GitHub Actions roda os testes automaticamente.

A publicação de versões (aumentar `migrador/versao.py` na `main`) fica com os mantenedores.

## Princípios do projeto

- **Nunca arriscar os dados:** o iPhone antigo não é alterado, e o iPhone novo só é tocado depois do backup verificado.
- **Dizer a verdade ao usuário:** se algo não é garantido, o programa diz isso.
- **Privacidade:** nenhum conteúdo dos aparelhos sai do PC do usuário. Eventos anônimos só com consentimento; nunca envie nomes, números de série, telefones (os caminhos de anexos do WhatsApp têm números de contatos) ou senhas. Passe todo texto livre por `diagnostico.anonimizar()`.

## Licença das contribuições

Ao contribuir, você concorda que sua contribuição será distribuída sob a mesma licença do projeto, a [GPL-3.0-or-later](LICENSE).
