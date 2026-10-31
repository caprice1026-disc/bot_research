# Pre-IPO DEX 間の相対価値研究（2026-10-31）

OpenAI の **Aster USDT 買い / Lighter 売り**を主対象に、Anthropic を補助対象として価格差の持続、IPO 処理、Funding、サイズ別の入口・出口コストを調べた。契約単位を外生的に揃えても価格差は残るが、即時利益確定の裁定とは確認できていない。

## 読み順

| 文書 | 内容 |
| --- | --- |
| [SUMMARY_JA.md](SUMMARY_JA.md) | 結論、履歴、板、保有・出口シナリオ |
| [ADDITIONAL_CANDIDATES.md](ADDITIONAL_CANDIDATES.md) | USD1経路、XYZ/Paragon、Oura、Aster内の132条件比較 |
| [METHODS.md](METHODS.md) | 換算式、列の意味、数量・費用・損益の計算条件 |
| [CONTRACTS.md](CONTRACTS.md) | 契約単位、価格形成、IPO、Funding 制度の確認 |
| [LIMITATIONS.md](LIMITATIONS.md) | 未検証事項とローカルデータ保存の失敗 |
| [SOURCES.md](SOURCES.md) | 一次資料と、各主張に対応する出典 |

## 前段の広範比較

前段研究は Aster、Lighter、XYZ、Paragon、io 等を比較し、合計 **37,804 行**の価格履歴を取得・検証したとの研究担当の報告に基づく。取得可能な最長期間は Aster OpenAI の **2026-05-26 以降**。Lighter は6月以降、io は8〜9月以降、XYZ ANT は10月26日以降など、市場ごとに異なる。全市場が同期間・同種の価格を提供するわけではない。

Mark と約定足は分離し、無取引による持越し足を約定比較から除外した。欠損補間、価格比に合わせた倍率調整、平均差の除去、100 始まりの正規化は行っていない。詳細な全市場の開始・終了時刻や実CSV列名は、未転送の元データからこのPCでは再確認できていない。

XYZ ANT は Class A の実株1株型なので、10億株仮定の市場と比較するには外部資料の株数が必要。Paragon は株式クラス未確認で条件付き比較。io の専用契約仕様は独立確認できず、提供説明に基づく仮定として分離した。Extended、edgeX、Variational 等も、単位未確認のものを価格の類似だけで換算していない。

前段では OpenAI の Aster 対 Lighter の差が10月の比較可能な726時間すべてに残った。今回、その持続性と費用・制度面を追加精査した。前段の板と今回の板は取得時刻が異なるため、同一スナップショットとして混ぜない。

## 追加候補（12:52〜12:55 UTC）

132条件を比較した追加研究では、AnthropicのAster USD1買い / Lighter売りが条件付きで残った。Ouraの54条件とAster内USDT/USD1の36条件は全入口差が負。XYZ/Paragonの微小な入口差にも往復費用と契約同一性の問題があり、OpenAI USD1は既存USDT経路より優先する根拠がなかった。

追加計算は独立FXのbid/askで各脚の元通貨PnLを評価したが、FXイベント時刻は約794〜1,000秒古く、Funding・交換・FXヘッジ・借入費用を含めない。**無条件の実行候補へ昇格した組合せはない**。詳細と時刻別の条件は [ADDITIONAL_CANDIDATES.md](ADDITIONAL_CANDIDATES.md) を参照。

## 証拠と保存状態

このMarkdown群は、同じ研究タスクの研究担当から受領した精査済み集計結果を整理したもの。契約文書の主要記述を再確認した一方、**元データのこのPCへの転送は失敗しており、計算はこのPCで未再現**である。

ローカル保存先は既存 `bot_research/research/preipo-cross-dex-20261031/`。元データ・Excel・ZIP・スクリプト・ログは `local-data/` に置く設計で、ローカルの `.git/info/exclude` で除外済み。GitHub の提出対象は上表の文書と本 README の **Markdown 7ファイルのみ**。

前段Excel・ZIPと統合ZIPの初回転送は `download failed` で失敗した。ユーザー依頼による統合ZIPの再確認ではTLSを検証付きで解決したものの、Windows版Pythonの `os.setxattr` 未対応により正式保存が止まった。追加候補ZIPは手動取得用で、今回は再転送していない。いずれもこのPCには未保存で、ハッシュ・展開内容の検証も未完了。詳細は [LIMITATIONS.md](LIMITATIONS.md)。本研究で売買、注文送信、ウォレット接続は行っていない。
