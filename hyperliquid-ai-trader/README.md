# Hyperliquidテストネット接続確認

リポジトリ直下の `.env` にある次の設定を使い、Hyperliquidテストネットへの接続を確認します。

```dotenv
HL_test_wallet=0x...
HL_test_wallet_private_key=0x...
```

確認内容は、公開Info APIによる市場メタデータ、対象アカウント状態、署名者の `userRole` の取得、および公式Python SDKによる署名付き `noop` の送信です。注文、送金、レバレッジ変更は行いません。`noop` は資産や注文を変更しませんが、実行のたびに署名者のnonceを一つ消費します。

## セットアップ

PowerShellでこのディレクトリへ移動し、専用仮想環境を作成して依存を導入します。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

このPCのPython 3.13で `ensurepip` が一時ディレクトリ権限により失敗する場合は、システムpipから対象Pythonを指定できます。

```powershell
python -m pip --python .venv\Scripts\python.exe install pip
python -m pip --python .venv\Scripts\python.exe install -e ".[test]"
```

## テスト

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 実接続

このディレクトリから次を実行します。

```powershell
.\.venv\Scripts\python.exe -m hl_testnet_check.cli `
  --env-file ..\.env `
  --output results\connection_check.json
```

成功時は終了コード0となり、`overall_status`、公開API結果、署名付き `noop` 結果が標準出力と `results/connection_check.json` に記録されます。ウォレットアドレス、秘密鍵、署名、API応答の残高やポジション詳細は出力しません。

`HL_test_wallet` と秘密鍵由来の署名者が異なる場合、コードは `userRole` が `agent` であり、その `user` が `HL_test_wallet` と一致することを確認します。紐付けが一致しなければnonceを消費せず終了し、一致する場合だけ署名付き `noop` を送ります。

## 参照仕様

- [Hyperliquid API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api)
- [Exchange endpoint / noop](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint)
- [公式Python SDK](https://github.com/hyperliquid-dex/hyperliquid-python-sdk)
