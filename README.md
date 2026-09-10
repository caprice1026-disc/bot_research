# Bot Research Repository

暗号資産市場の取引戦略に関する再現可能な定量研究を行うリポジトリです。AI/強化学習に依存しない、実データに基づいた分析基盤を構築しています。

## 研究プロジェクト一覧

### 1. **Polymarket BTC 5分市場 - リードラグ研究**
`Polymarket_5m_btc/`

**概要：** Polymarketの公開データを使用し、外部BTC取引所（Binance、Coinbase、Hyperliquidなど）とPolymarket BTC Up/Down 5分市場間の短期リードラグ関係を研究します。

**主な特徴：**
- 公開APIのみを使用（注文発注・ウォレット操作なし）
- 複数取引所からの同時データ収集
- SQLite WALモードによる並行書き込み対応
- 受信時刻ベースのリードラグ分析
- 選定マーケットの意思決定区間の連続性検証

**成果物：** 品質検証レポート、リードラグ分析結果、マーケット適格性判定

---

### 2. **Base Uniswap v3 LP戦略 - バックテスト研究**
`base-lp-research/`

**概要：** Base チェーン上の WETH/USDC 0.05% Uniswap v3 プールを対象に、流動性提供戦略の再現可能な研究基盤を構築します。

**主な特徴：**
- AI/強化学習を使わない再現可能な設計
- Swapイベントの時系列リプレイ
- L2実行費、L1セキュリティ費、スリッページ/MEVを含むコストモデル
- 指標：Gross APRではなく、HODL相対のターミナルネットアルファ、最大ドローダウン、レンジ内滞在時間
- パラメータグリッドサーチ（3幅 × 3閾値）

**検証条件：**
- 9か月未満の分析ウィンドウは `insufficient_data` とマーク
- 実LPの所有者情報は使用せず、仮想的小額LPとして扱う
- 金融助言や実取引エグゼキューターではない

---

### 3. **Binance BTCUSDT先物 - レジーム研究**
`binance-btcusdt-futures-research/`

**概要：** Binance公開データを使用したBTCUSDT USDT-M無期限先物の取得・EDA・HMM/GMM/PELT・時点整合ウォークフォワード検証。

**主な手法：**
- 隠れマルコフモデル（HMM）によるレジーム検出
- ガウス混合モデル（GMM）
- PELT（Pruned Exact Linear Time）による変化点検出
- ウォークフォワード検証

---

### 4. **Hyperliquid AI トレーダー**
`hyperliquid-ai-trader/`

**概要：** Hyperliquidデリバティブエクスチェンジ向けのAIベース自動取引システム。

---

### 5. **天気予測 Polymarket研究**
`weather-polymarket-research/`

**概要：** 天気関連イベントのPolymarket予測市場に関する研究。

---

## リポジトリの設計原則

### 再現可能性
- 環境変数・設定ファイルによる外部依存の明示化
- RPC/APIレート制限への対応（チャンク分割、リトライ機構）
- 冪等な取得・検証・レポート生成

### 透明性
- 各プロジェクトに BlobSha/チェックサムの記録
- データ状態の明示（`ok`/`partial`/`insufficient_data`など）
- コストモデル・仮定の詳細ドキュメント化

### 保守性
- 公開フィード のみ使用（認証情報不要）
- SQL + Parquetによる分析基盤
- Pytest + Ruff + Pyrightによる品質管理

---

## クイックスタート

### 環境準備

```powershell
# 例：Polymarket 5m研究
cd Polymarket_5m_btc
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pip install -e .
```

### 基本的な実行フロー

1. **データ収集** - 公開フィードから市場データを取得
2. **選別** - マーケット適格性を検証
3. **コンパクト化** - SQLite → Parquetに変換
4. **検証** - 品質チェック・統計検証
5. **分析** - リードラグ・バックテスト・レジーム分析実行
6. **報告** - マシンリーダブル＋人間向けレポート生成

---

## 重要な注記

⚠️ **このコードは金融助言ではありません。実取引エグゼキューターではありません。**

- バックテスト結果は過去データに基づいており、将来のパフォーマンスを保証しません
- 手数料・スリッページ・執行・実LPダイナミクスは簡略化されています
- 長期パイロット段階の研究です

---

## ドキュメント

- `PLANS.md` - 実装計画と進捗
- `AGENTS.md` - AI/コーディングエージェント関連の記録
- `.agent/` - 実装詳細の追跡

---

## ライセンス

MIT License
