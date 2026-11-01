# Bot Research Repository

暗号資産・予測市場の取引戦略を、公開データとシミュレーションで検証するリポジトリです。固定ルールの比較に加え、LLMによる売買判断や機械学習・強化学習の実験も含みます。

各プロジェクトは独立しており、必要なPython環境・入力データ・実行方法が異なります。下の一覧から対象のREADMEを確認してください。

## プロジェクト一覧

| プロジェクト | 主な内容 | 実行範囲 |
| --- | --- | --- |
| [Polymarket BTC 5分市場](Polymarket_5m_btc/README.md) | 外部BTC価格と予測市場の反応の時間差を分析 | 公開データ収集・分析 |
| [Polymarket 5戦略](polymarket-strategy-lab/README.md) | BTC・天気市場の5戦略と天気予報差の追加検証 | バックテスト |
| [天気予測市場](weather-polymarket-research/README.md) | New Yorkの日最高気温、GEFS予報、観測、価格を照合 | 公開データ収集・バックテスト |
| [Base LP](base-lp-research/README.md) | WETH/USDC 0.05% Uniswap v3の流動性提供を、保有継続と比較 | バックテスト |
| [Binance BTCUSDT先物](binance-btcusdt-futures-research/README.md) | データ取得、HMM/GMMによる相場局面の分析、月次検証 | 公開データ収集・分析 |
| [JPYC裁定](jpyc-arbitrage-research/README.md) | PolygonのJPYC/USDC、償還・資金再利用・LP・為替の比較 | 公開RPCの読み取り・シミュレーション |
| [Pre-IPO DEX比較](research/preipo-cross-dex-20261031/README.md) | OpenAI・Anthropicなどの契約、価格差、保有・出口費用を調査 | 調査資料。元データはこのPCに未保存 |
| [Hyperliquid AI Trader](hyperliquid-ai-trader/README.md) | Geminiの判断を固定リスク制限で検証し、注文を実行 | Testnet専用。Mainnet設定は拒否 |
| [LLMポジション管理](llm-position-management/README.md) | 保有継続・追加・縮小・決済とGeminiの判断を比較 | オフライン再生・予算付きAPI比較。発注なし |
| [継続保有型BTC bot](llm-position-management-stg/README.md) | Mainnet公開データによる仮想売買とCloud Run向けアプリ | 接続前の初期実装。Testnet注文検証経路あり |
| [SwingGuard適応](swingguard-research/README.md) | BTC/ETHの日次配分と5分ごとのリスク管理を比較 | 保存済み成績は固定ルール。Geminiは任意 |
| [EvoQuantLab適応](evoquantlab-research/README.md) | BTC/ETHの時系列因子を式から探索 | オフライン。LLM通信・発注なし |
| [決済ルール比較](exit-policy-research/README.md) | 固定の買付条件で、固定決済・MLP・強化学習を比較 | オフライン。外部通信・発注なし |
| [Trading core](trading-core/README.md) | 目標建玉との差分計算、費用計算、売買シミュレーター | 実験用の共通部品 |

SwingGuard・EvoQuantLab・決済ルール比較は、原論文を暗号資産価格データ向けに変更した実装です。原論文の完全再現やLLMの性能検証ではありません。

## セットアップと動作確認

Pythonの対応バージョンと依存関係は、各プロジェクトの`pyproject.toml`や`requirements`ファイルに記載しています。ルートに全プロジェクト共通のインストール手順はありません。

以下はPolymarket BTC 5分市場の例です。Python 3.13を用意し、リポジトリルートからPowerShellで実行してください。

```powershell
cd Polymarket_5m_btc
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m btc5m --help
.venv\Scripts\python.exe -m btc5m fixture --output-root .\data\fixture-run
```

`fixture`は外部通信なしで、保存・データ検証・分析レポートの生成を確認します。レポートは`exploratory`となり、実市場での収益性は示しません。データ収集と開発用の検査コマンドは[Polymarket BTC 5分市場のREADME](Polymarket_5m_btc/README.md)にあります。

LLMやクラウドを使う実験には、APIキーなどの設定が別途必要です。環境変数の名前と読み込み方法は対象のREADMEと`.env.example`を参照してください。秘密情報はGitへ保存しないでください。

## データと結果の扱い

- 生データや大きな生成物の多くはGit管理外です。新しいcheckoutでは各READMEの手順で入力を取得・生成し、保存済みのハッシュや検証結果と照合してください。
- 対象市場・期間・設定を固定し、取得済みのデータや完了した処理を再利用します。条件を変える場合は別の実験として記録します。
- 欠損を捏造して埋めず、`partial`や`insufficient_data`などの状態と制約を記録します。成果物の検証成功と、収益評価に十分なデータがあることは別です。
- データ不足によるゼロ取引や、オフラインの動作確認を収益評価の成功とは扱いません。手数料、価格差、約定時刻などの仮定は各レポートを確認してください。

本リポジトリは金融助言を目的としません。過去の結果は将来の成績を保証せず、シミュレーションには実際の板や約定を再現できない制約があります。

## 開発用ドキュメント

- [AGENTS.md](AGENTS.md): 作業方針と研究・実行の検証ルール
- [PLANS.md](PLANS.md): ExecPlanの作成・更新方法
- [.agent/](.agent/): 各作業の計画、進捗、判断の記録
- [docs/](docs/): 設計資料と実装計画

## ライセンス

[MIT License](LICENSE)。参照元コードや外部データの利用条件は、各プロジェクトの出典・ライセンス記録を確認してください。
