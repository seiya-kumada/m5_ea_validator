# UBS現在結果・暫定総合報告書 作成計画

## 目的

完了済みのUBS Gold 7戦略比較だけを使用し、追加バックテストを行わず、目的・結果・詳細の順で読める暫定総合報告書を作成する。

## 使用する正本

- 四半期比較28件: `data/ubs_strategy_comparison/quarterly_1m_ohlc/20260902T155309727908_b95bac15/`
- 約1年モデリング方式比較14件: `data/ubs_strategy_comparison/model_comparison_1y/20260902T162029093739_4e2ec09f/`
- UBS比較の計画・制約: `doc/plans/20260902_ubs_gold_strategy_comparison.md`
- ティック補完許容の決定: `doc/decisions/20260902_ubs_model_comparison_sparse_ticks.md`

## 実装方針

1. CSVとmanifestを読み、28件・14件・7ペア・実ティック品質7件の完全性を検証する。
2. Markdown報告書を`doc/reports/ubs_gold_strategy_comparison.md`へ生成する。
3. 次のSVGを`doc/reports/assets/ubs_gold_strategy_comparison/`へ生成する。
   - 約1年Net ProfitのOHLC／実ティック比較
   - 実ティックによるNet Profit変化
   - 四半期別Net Profitヒートマップ
   - 実ティックのNet ProfitとEquity DD
4. グラフには大きめの文字、数値の直接表示、明暗テーマ対応、スクリーンリーダー向け説明を含める。
5. 原setのリスク量が異なるため、最終順位ではなく「原設定の特徴」として解釈する。

## テスト方針

- 不完全な件数・失敗manifest・不足モデルを拒否する。
- 生成Markdownに目的・結果・詳細、全7戦略、4画像リンクが存在することを確認する。
- SVGにtitle、desc、viewBox、明暗テーマCSS、十分な文字サイズが存在することを確認する。
- 実データで生成後、リンク先、表の件数、主要数値を正本CSVと突合する。

## 変更履歴

- 2026-09-03: 現在完了しているPhase 0～2だけを対象とする暫定報告書として開始した。Phase 3以降の同一リスク・遅延・資金耐性は未実施であり、結論へ混在させない。
- 2026-09-03: 2枚目のグラフが示す差の定義を単なる系列名ではなく、`実ティックのNet Profit − 1 minute OHLCのNet Profit`という引き算の式でグラフ内に明記する方針へ変更した。正負の意味をグラフ単体で判断できるようにするためである。
- 2026-09-03: 四半期別グラフの各WF見出しに開始日と終了日を追記する方針へ変更した。本文の固定条件まで移動しなくても、グラフ単体で各期間を確認できるようにするためである。
