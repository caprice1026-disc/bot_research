"""Render the later-period findings only after local verification succeeds."""
import hashlib
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "results/weather_followup_20261012"
LABELS = {"frozen_original": "前回GFS＋ECMWF（fit固定）", "ncep_nbm_conus": "NOAA/NWS NBM（Open-Meteo経由）",
          "gfs_global": "GFS単独", "ecmwf_ifs025": "ECMWF IFS 0.25°単独", "persistence": "過去7日の観測平均（fit固定）"}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def interval(values):
    return "未評価" if values[0] is None else f"[{values[0]:+.2f}, {values[1]:+.2f}]"


def loss_attribution(report):
    """Describe verified selected fills; this is not a new fitted strategy."""
    output = {"scope": "Post-result description of primary selected fills only", "providers": {}}
    for model in ("frozen_original", "ncep_nbm_conus"):
        path = OUT / model / "primary_fills.json"
        fills = read(path)
        sides = {}
        for side in ("Yes", "No"):
            rows = [f for f in fills if f["outcome"] == side]
            sides[side] = {"fills": len(rows), "wins": sum(int(f["settlement"]) for f in rows),
                           "net_pnl": round(sum(f["pnl"] for f in rows), 5),
                           "mean_predicted_win_probability": statistics.mean(f["model_probability"] for f in rows),
                           "observed_win_rate": statistics.mean(f["settlement"] for f in rows)}
        if abs(sum(s["net_pnl"] for s in sides.values()) - report["aggregates"][model]["primary"]["net_pnl"]) > 1e-7:
            raise ValueError("Side attribution differs from verified total")
        output["providers"][model] = {"source_path": str(path.relative_to(ROOT)),
                                       "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "sides": sides,
                                       "sigma_by_city_f": {city: c["providers"][model]["fit"]["sigma_f"] for city, c in report["city_results"].items()}}
    (OUT / "loss_attribution.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def main():
    report = read(OUT / "summary.json")
    validation = read(OUT / "verification.json")
    if validation["status"] != "passed":
        raise ValueError("Successful validation required")
    if hashlib.sha256((OUT / "summary.json").read_bytes()).hexdigest() != validation["summary_sha256"]:
        raise ValueError("Verified summary changed")
    attribution = loss_attribution(report)
    cfg = report["config"]
    original = report["aggregates"]["frozen_original"]["primary"]
    nbm = report["aggregates"]["ncep_nbm_conus"]["primary"]
    control = report["aggregates"]["persistence"]["primary"]
    books = read(ROOT / "data/weather_followup_20261012/books-manifest.json")
    metadata = read(ROOT / "data/weather_followup_20261012/metadata-manifest.json")
    lines = ["# 天気戦略の後続7日と予報サービス比較", "", "実施日: 2026-10-12（日本時間）。実注文なし。金額は担保通貨のドル相当。", "",
             "## 結果と判断", "",
             f"前回に含まれない2026-10-04〜10-10の3都市・21都市日・231市場を再研究した。前回モデルの重み・偏り補正・誤差幅・売買ルールを変更しない主要結果は、{original['fills']}仮想約定、純損益 **{original['net_pnl']:+.5f}ドル**。同じ期間の過去7日平均は{control['fills']}約定・{control['net_pnl']:+.5f}ドルだった。前回の72都市日・+35.60954ドルを今回の未使用期間へ合算していない。", "",
             f"追加したNOAA/NWSのNBMは{nbm['fills']}仮想約定・**{nbm['net_pnl']:+.5f}ドル**。NBMは米国向けに複数モデルを偏り補正・統合した数値予報で、NWSの予報官が調整した最終地点予報とは異なる。[NWSの説明](https://www.weather.gov/news/200318-nbm32)、[使用API](https://open-meteo.com/en/docs/gfs-api)。", "",
             "**前回の利益は後続期間で再現せず、5つの比較案すべてが損失となった。現条件での実運用や有料予報サービスの採用を支持する結果ではない。** 7暦日の結果から将来の損失を断定することもできない。表示板での仮想約定で、実際の注文受理・約定・待ち順は検証していない。サービス比較は今回追加の探索的評価であり、この結果を見て最良の都市別モデルへ差し替えてはいない。", "",
             "## 同じ条件で比較した損益", "",
             "各案は独立した仮想口座であり、5案の損益を合算しない。各都市1,000ドル、合計3,000ドル。1都市1日1試行・5株。以下は手数料と1セントの不利な価格差を控除済み、API契約料・サーバー費用・送金費用は含まない。", "",
             "| 予報入力 | 約定 / 勝ち | 純損益 USD | 資金収益率 | 3日ブロック95%区間 USD |", "|---|---:|---:|---:|---:|"]
    for model, label in LABELS.items():
        s = report["aggregates"][model]["primary"]
        lines.append(f"| {label} | {s['fills']} / {s['wins']} | {s['net_pnl']:+.5f} | {s['capital_return_pct']:+.4f}% | {interval(s['three_day_block_bootstrap_pnl_95pct'])} |")
    lines += ["", "日付を揃えた損益差も確認した。都市を同じ暦日にまとめ、3日単位の循環ブロックで5,000回再標本化した。21都市日を21独立標本として扱わない。**区間は7暦日しかないため参考値であり、強い統計的証拠にならない。**", "",
              "| 比較 | 追加損益 USD | 日次差の95%区間 USD |", "|---|---:|---:|"]
    for key, result in report["paired_comparisons"].items():
        left, right = key.split("_minus_")
        lines.append(f"| {LABELS[left]} − {LABELS[right]} | {result['net_pnl_difference']:+.5f} | {interval(result['three_day_block_95pct'])} |")
    lines += ["", "## 地点別の結果と予報精度", "", "気温の平均絶対誤差（MAE）は8月校正の偏り補正後の予報とIEM定時観測の最高値との差。NBMにも同じ過去期間だけから追加の偏り補正・誤差幅推定を施しており、配信値をそのまま使うサービス一般の成績とは異なる。補正前の誤差はsummary.jsonのraw_uncorrected_mae_fに保存した。区間の確率評価と払戻しは公式決着を基準にする。予報精度と取引損益は別の指標である。", "",
              "| 地点 | 前回モデル損益 | NBM損益 | 7日平均損益 | 前回MAE °F | NBM MAE °F | ECMWF単独MAE °F |", "|---|---:|---:|---:|---:|---:|---:|"]
    for city, c in report["city_results"].items():
        p = c["providers"]
        amounts = [p[m]["scenarios"]["primary"]["summary"]["net_pnl"] for m in ("frozen_original", "ncep_nbm_conus", "persistence")]
        errors = [p[m]["forecast_quality"]["mae_f"] for m in ("frozen_original", "ncep_nbm_conus", "ecmwf_ifs025")]
        lines.append(f"| {city} | {amounts[0]:+.5f} | {amounts[1]:+.5f} | {amounts[2]:+.5f} | {errors[0]:.3f} | {errors[1]:.3f} | {errors[2]:.3f} |")
    lines += ["", "NBMはChicagoとNYCの温度誤差を改善したが、Atlantaでは前回モデルより悪化した。NYCでは温度誤差の改善と取引損失の拡大が同時に起きた。この観測から全地点の標準モデルへ昇格させない。ECMWFの[2026年9月の公式評価](https://www.ecmwf.int/en/elibrary/81748-evaluation-ecmwf-forecasts)も、短期の地表変数では他センターが一部先行すると説明している。広域の中期予報評価を、特定空港の当日最高気温区間へ直接適用できない。", "",
              "## 損失の内訳と確率推定", "",
              "| モデル / 購入側 | 約定 / 勝ち | 平均推定勝率 | 実現勝率 | 純損益 USD |", "|---|---:|---:|---:|---:|"]
    for model, details in attribution["providers"].items():
        for side, values in details["sides"].items():
            lines.append(f"| {LABELS[model]} / {side} | {values['fills']} / {values['wins']} | {values['mean_predicted_win_probability']:.1%} | {values['observed_win_rate']:.1%} | {values['net_pnl']:+.5f} |")
    max_center_probability = math.erf(1 / (2 * math.sqrt(2)))
    lines += ["", "主な損失はNoの購入で生じた。前回モデルのNoは13約定に平均76.9%の勝率を見積もったが、実際の勝ちは2件だった。NBMのNoも平均75.1%に対して1勝だった。これは今回選ばれた小標本の事後診断であり、モデル全体の勝率推定の検定とは区別する。公式勝者、選んだNo token、実askを照合済みで、反対側の価格からNoを合成した結果ではない。", "",
              f"確率変換は正規分布で、標準偏差を少なくとも2°Fとする前回ルールを維持した。この場合、2°F幅の区間の中心へ予報平均が一致してもYes確率は最大約{max_center_probability:.1%}、No確率は少なくとも約{1-max_center_probability:.1%}となる。市場のNo価格が低ければ、精度の高い平均気温予報でもNoを割安と判定し得る。気温の平均だけを改善しても、誤差幅・区間確率が市場より正確になるとは限らない。今回の損失を見て誤差幅を狭め、改善した損益を未使用期間の成果として示すことはしていない。", "",
              "具体例はNYCの10月5日、70–71°F区間。NBMの補正後予報平均は70.41°Fと区間内だが、No勝率66.2%と計算し、No ask 0.43に対して購入候補となった。公式決着はこの区間のYesで、Noは損失となった。サービスの気温予報と取引向け確率分布を分けて検証する必要がある。[内訳JSON](../results/weather_followup_20261012/loss_attribution.json)。", "",
              "## 執行条件の感度", "", "主要モデルとNBMについて一要素だけ変更した結果。購入候補も変わるため、同じ約定へ費用だけ追加した比較ではない。最良の感度条件を主要結果として選び直さない。", "",
              "| 条件 | 前回モデル 約定 / 損益 USD | NBM 約定 / 損益 USD |", "|---|---:|---:|"]
    for name in report["aggregates"]["frozen_original"]:
        a, b = (report["aggregates"][m][name] for m in ("frozen_original", "ncep_nbm_conus"))
        lines.append(f"| {name} | {a['fills']} / {a['net_pnl']:+.5f} | {b['fills']} / {b['net_pnl']:+.5f} |")
    lines += ["", "## 信頼できるオンライン予報サービスを使う場合", "",
              "**NOAA/NWS NBM:** 今回、Open-Meteo Single Runs経由で初期時刻を指定した履歴を実際に比較した。公的機関の統合予報を使う利点はあるが、今回の損益と地点別誤差が判断材料であり、ブランドの信頼性だけで利益を保証しない。NWSの公開地点APIで取得する最終予報は別の入力で、今回のNBM結果と同一視しない。", "",
              "**ECMWF・GFS / Open-Meteo:** 前回から使用済み。今回単独案も同一条件で比較した。Open-Meteoは配信サービス、ECMWF・NOAAはモデルの供給元である。無料の評価用APIと商用ライセンスは別であり、運用時の契約料金は未確認。[料金・利用区分](https://open-meteo.com/en/pricing)。", "",
              "**Weather.com / The Weather Company:** ForecastWatchによる広域・複数項目の精度実績を公表している。ただし当該研究は同社の委託で、ここで必要なKORD/KLGA/KATLの当日最高気温区間での利益を証明しない。[精度説明と委託注記](https://www.weathercompany.com/news/twco-widens-lead-as-worlds-most-accurate-forecaster/)。公式APIは有効なキーとアカウントを要求する。今回、利用可能なキー、当時の発表時刻付き予報履歴、費用条件を確保できていないので**損益は未評価**。[API利用手順](https://developer.weather.com/docs/getting-started)。", "",
              "**AccuWeather:** [公式FAQ](https://developer.accuweather.com/faq)はDeveloper PortalとEnterprise APIのいずれでも過去の予報を提供しないと明記する。過去の観測を予報として入力すれば将来参照になるため使用していない。従って**今回の過去損益は未評価**。将来比較には、契約・利用許諾の範囲で予報の発表時刻、取得時刻、指定空港、当日最高気温を事前保存する必要がある。", "",
              "収益の成立条件は、予報サービスの信頼性だけでなく、買える価格と数量に対する確率差である。本研究では購入費用控除後8ポイント以上を要求し、板の鮮度・執行後の価格悪化も確認する。市場参加者が同じ予報を価格へ反映していれば、温度誤差を小さくしても利益は増えない。確率分布への変換と校正、地点・丸め・決着ルールも必要になる。", "",
              f"今回のNBMの前回モデルに対する追加損益は **{report['paired_comparisons']['ncep_nbm_conus_minus_frozen_original']['net_pnl_difference']:+.5f}ドル/7日**。同じ7日間に追加サービス料金Cドルを割り当てれば追加利益はこの値からCを引いた額となる。今回の実測では、料金ゼロでも前回モデルに対する増分は負であり、追加料金を回収できる証拠はない。これは月額料金の回収予測ではなく、標本が短いため翌月や別の有料サービスへ外挿できない。大口化も表示数量で棄却されるため5株の成績を比例拡大できない。", "",
              "次の研究で有料サービスを比較するなら、当該サービスの配信予報を市場の判断前に保存し、同じ空港・最高気温・発表時刻・利用可能時刻を揃える。確率校正と採用ルールを別の過去期間で固定し、今回に重ならない将来期間で7日平均と比較する。この保存や契約は今回実行していない。", "",
              "## データ・固定条件・制約", "",
              "収集に使ったサービス・ツールは次のとおり。API料金と研究の取得・処理ツールを分けて記録する。", "",
              "| 用途 | 使用したサービス・ツール |", "|---|---|",
              "| 当時の予報 | Open-Meteo Single Runs API。00UTC初期のGFS・ECMWF・NBM |",
              "| 空港観測 | Iowa Environmental Mesonet（IEM）の定時METAR CSV |",
              "| 市場・ルール・Yes/No token | Polymarket Gamma API |",
              "| 公式決着・払戻し | Polymarket Data API v2 |",
              "| 実際の売買板 | Pendulum Flow公開Parquet。Python requestsのHTTP Range＋PyArrowで対象市場だけ抽出 |",
              "| 評価・検証 | Python3.12、既存の板復元runner、Decimal会計、SHA256 |", "",
              f"取得メタデータは{len(metadata['days'])}都市日（8月校正90＋後続21）、評価対象231市場・462 outcome。部分Parquetは{sum(len(d['parts']) for d in books['days'])}本、行数{sum(p['rows'] for d in books['days'] for p in d['parts']):,}、初期スナップショット欠損{books['missing_initial_snapshots']}。取得失敗日を削除せず、全21都市日を評価した。", "",
              "校正は2026-08-01〜08-30。主要モデルと7日平均は前回fitをそのまま使用。NBM・単独モデルは同じ30日に限定して校正し、10月の正解で再学習しない。00UTC予報runを6時間後に公開済みと仮定し、現地午前10時に判断。発表時刻の実測履歴はない。[Single Runsの初期時刻と公開遅延の説明](https://open-meteo.com/en/docs/single-runs-api)。", "",
              "ChicagoはKORD・UTC−5、NYCはKLGA・UTC−4、AtlantaはKATL・UTC−4。対象期間は夏時間内で、都市中心点に置き換えない。予報グリッドは指定空港座標への配信値で、空港の直接観測と同一ではない。60秒後の実際の選択tokenのask・5株以上の表示数量を用い、受信時刻・配信元時刻とも鮮度60秒以下を要求する。手数料は[現在の公式仕様](https://docs.polymarket.com/trading/fees)の株数×0.05×p×(1−p)、5桁丸め。過去に実際に請求された費用の復元とは区別する。", "",
              "Pendulum FlowのCC BY 4.0公開板を使用。受信時刻は配布側の時刻である。HTTP429に対応して、対象行グループを1要求へまとめて読み、成功したRangeと部分Parquetのhashを保存した。全原本の配布hashは検証していない。元研究を上書きせず、原本は別rootに保存した。", ""]
    for city, c in report["city_results"].items():
        lines.append(f"- {city}: 校正・評価の観測注意事項 `{json.dumps(c['issues'], ensure_ascii=False)}`。7日平均の参照履歴の注意事項 `{json.dumps(c.get('control_history_issues', []), ensure_ascii=False)}`。")
    lines += ["", "後続21都市日は定時観測各24時間で、観測最高値と公式勝者区間の不一致は0。空港の定時METAR最高値と公式勝者区間は校正期間で一致しない日がある。欠損・不一致を補間していない。7日平均にも過去観測の不足がある日は明記し、その対照を完全な観測に基づく成績と呼ばない。払戻しは一貫して公式決着を使う。", "",
              "当時のtick・最小注文数の履歴は確認できていない。追加1セントは不利な費用のバッファであり、当時のtickに適合した指値注文の価格を再現したものではない。表示askと数量の確認から、競合注文や注文受理を含む実約定の確認へ読み替えない。", "",
              "## 検証と再実行", "",
              f"回帰テスト57件成功。対象期間・token同一性・前回fit・校正境界・受信時刻・選択規則・実ask数量・公式払戻し・独立Decimal会計・入力hashを確認し、{validation['scenario_fills_checked']} scenario約定を照合した。成果物検証の成功は実約定・収益性の保証ではない。", "",
              "実行環境はPython3.12.14、DuckDB1.5.6＋署名済httpfs、PyArrow21.0.0。依存は研究用`.probe-deps`内に置く。Python3.10は公式APIの`+00`日時形式を扱えないため使用しない。リポジトリルートで次を実行する。", "",
              "    $weatherPython = 'C:/Users/81905/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'",
              "    & $weatherPython polymarket-strategy-lab/setup_runtime.py",
              "    & $weatherPython -m pip install --target polymarket-strategy-lab/.probe-deps -r polymarket-strategy-lab/research/weather_followup_20261012/requirements.txt",
              "    & $weatherPython polymarket-strategy-lab/weather_followup.py collect --workers 4",
              "    & $weatherPython polymarket-strategy-lab/weather_followup.py books --workers 2",
              "    & $weatherPython polymarket-strategy-lab/weather_followup.py evaluate",
              "    & $weatherPython polymarket-strategy-lab/weather_followup.py verify",
              "    & $weatherPython polymarket-strategy-lab/research/weather_followup_20261012/write_report.py",
              "    & $weatherPython -m unittest discover -s polymarket-strategy-lab/tests -v", "",
              "成功済みキャッシュは再取得しない。条件を変更する場合は別設定・保存先に分ける。旧cohortのテスト入力がないcheckoutでは`research/weather_followup_20261012/restore_test_inputs.py`で市場情報と決着だけを復元する。取得に使用した依存をサンドボックス内で読めない環境では、承認済み取得時と同じ実行環境でテストとオフライン評価を行う。", "",
              "結果: [summary.json](../results/weather_followup_20261012/summary.json)、[verification.json](../results/weather_followup_20261012/verification.json)、[主要約定CSV](../results/weather_followup_20261012/frozen_original/primary_fills.csv)、[NBM約定CSV](../results/weather_followup_20261012/ncep_nbm_conus/primary_fills.csv)。公式資料一覧は[調査出典](weather_followup_20261012/sources.json)。", ""]
    target = ROOT / "research/WEATHER_FOLLOWUP_20261012_REPORT.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
