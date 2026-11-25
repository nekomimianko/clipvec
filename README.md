# clipvec — CLIP STUDIO PAINT のベクターレイヤーを筆圧ごと入出力する

CLIP STUDIO PAINT(以下 CSP)の `.clip` ファイルを直接読み書きし、ベクターレイヤーのストローク(座標・筆圧・傾き・速度・線幅/不透明度の倍率・色・ブラシ・曲線の種類)を欠落なく取り出したり、書き戻したりします。SVG 経由では失われる筆圧も残ります。

**解析した形式仕様: [docs/CLIP_FORMAT_SPEC.md](docs/CLIP_FORMAT_SPEC.md)**

* Python 3.10+ のみ(標準ライブラリだけで動作。`raster.py` だけ numpy を使用)

## Blender アドオン (Grease Pencil v3, Blender 4.3+ / 5.1 で確認)

1. `python tools/build_addon.py` → `dist/io_clip_vector.zip`
2. Blender: 編集 > プリファレンス > アドオン > 「ディスクからインストール」で zip を選び、有効化
3. **ファイル > インポート > CLIP STUDIO PAINT vector (.clip)**
   * ベクターレイヤー1枚が GP レイヤー1枚になります。色ごとにマテリアルを作ります
   * スプラインは Catmull-Rom、角のあるスプラインとベジェは Bezier(FREE ハンドル。形状は CSP と同じ)、直線は Poly になります
   * 点の `radius` / `opacity` には、CSP が実際に描く値(ブラシの筆圧設定まで反映)を入れます
   * CSP 固有の値は `csp_*` 属性として保持します(`csp_pressure`, `csp_tilt`, `csp_velocity`, `csp_width`, `csp_opacity`, `csp_random`, `csp_seed`, `csp_pflags` / ストローク側は `csp_flags`, `csp_brush`, `csp_radius` …)
4. **ファイル > エクスポート > CLIP STUDIO PAINT vector (.clip)**
   * `Update source .clip`: インポート元のファイルを土台に書き出します。ラスターレイヤーやブラシなど他の内容はそのまま残り、**編集していないストロークはバイト単位で元どおり**になります
   * `New document`: ベクターレイヤーだけの新しい .clip を作ります(サイズ・DPI を指定。元のブラシ設定もコピーします)
   * Blender で描いた線: 点の radius を CSP の筆圧として書き込みます(筆圧→線幅が線形で最小 0% のブラシを自動で作ります)。Bezier は CSP の3次ベジェになります
   * Blender で radius や opacity を変えた既存の線は、元の筆圧を残したまま線幅・不透明度の倍率で調整するので、CSP でも同じ見た目になります

座標: キャンバス 1px = 1/`Pixels per Unit` [BU](既定 1000)。XZ 平面(正面ビュー)に置き、`Center Canvas` ならキャンバス中心が原点になります。

## コマンドライン(他のアプリとの受け渡し用)

```
python -m clipvec info   in.clip
python -m clipvec export in.clip out.json [--lossless]
python -m clipvec import in.json out.clip [--base base.clip] [--append]
python -m clipvec svg    in.clip out.svg
```

JSON 形式は `clipvec/interchange.py` の先頭に記載しています。読み込み時に必須なのは `x`, `y` だけです。各点の `render_radius` / `render_opacity` は CSP の描画値を計算したもので、筆圧という概念が無いアプリでも線幅をそのまま再現できます。`--lossless` を付けると未知のバイトまで保持するので、JSON を経由しても .clip に戻せば元のレコードと完全に一致します。
SVG は線を塗りのアウトラインとして書き出すので、筆圧による線幅の変化が残ります(点ごとの不透明度の変化は平均値になります)。

## Python API

```python
from clipvec.document import ClipDocument
from clipvec.vector import Stroke, Point

doc = ClipDocument.load("in.clip")          # or ClipDocument.new(1920, 1080, dpi=350)
for layer in doc.vector_layers():            # 下のレイヤーから順に
    for s in layer.strokes:
        widths = doc.stroke_width_factors(s) # ブラシ設定込みの線幅倍率
        for p in s.sample(8): ...            # CSP と同じ補間で中心線をサンプリング

lid = doc.add_vector_layer("from python")
pts = [Point(100 + i * 8, 300, pressure=i / 100) for i in range(101)]
doc.set_strokes(lid, [Stroke(pts, color=(20, 20, 20), radius=6, brush_id=doc.default_brush_id())])
doc.save("out.clip")

# ラスターレイヤーの追加も可能（RGBA 画像をタイル化して同梱する）
import numpy as np
rgba = np.zeros((1080, 1920, 4), np.uint8)   # 塗りたい画素に (R, G, B, A)
rid = doc.add_raster_layer("塗り", rgba)       # 既定では最下層に入る
doc.save("out.clip")
```

## テストとツール

* `python tools/selftest.py` — `samples/` の全ファイルで往復変換がロスレスかどうか、新規作成の整合性を確認します(CSP・Blender 不要)
* `blender -b --factory-startup --python tools/blender_roundtrip.py -- in.clip outprefix` — Blender を経由した往復の誤差を確認します
* `tools/gui.py`, `tools/h.py` — 解析に使った CSP 自動操作(合成ペン入力で筆圧・傾き付きの線を描く、名前を付けて保存など)
* `tools/vdump.py` — ベクター本体のダンプ、`tools/dbdiff.py` — 2つのファイルの SQLite 差分

## 制限事項

* 動作確認は CSP PRO 1.13.2 で行いました。新しい版の 92 バイトヘッダは読み書きできますが、この環境では生成できず、未検証です
* ブラシの形状・テクスチャ・水彩などの再現はしません(Blender 側は丸い線になります)。ブラシ設定は ID で保持しているので、CSP に戻せば元どおりです
* 速度・ランダムによる線幅の変化は近似です
* アニメーションのセル(タイムライン)は対象外です。現在のフレームだけを扱います

