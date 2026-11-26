# CLIP STUDIO PAINT `.clip` ファイル形式 リファレンス

`.clip` を読み書きするツールを作るための技術リファレンスです。本リポジトリの実装(`clipvec/`)はこの文書のとおりに動きます。

* **非公式**です。CELSYS の公開仕様ではありません
* 検証環境: CLIP STUDIO PAINT PRO **1.13.2** (Windows 11 / ARM64)、`Project.ProjectInternalVersion = "1.1.0"`。検証日 2026-09-28
* 調べ方: CSP を自動操作(合成ペン入力で筆圧・傾き付きの線を描く)して保存し、ファイル同士の差分を取りました。さらに、値を書き換えたファイルを CSP で開き直し、描画結果を確かめています(§13)
* 本書の値はすべてビッグエンディアンです。例外(リトルエンディアン)の箇所は **LE** と明記します

## 本書の位置づけ

- 本書は、CLIP STUDIO PAINT のアプリケーション本体をリバース
  エンジニアリングしたものではない。解析の対象は、ユーザーが自ら
  作成して手元に保存した `.clip` ファイルのみであり、アプリケー
  ションのバイナリに対する逆コンパイル・逆アセンブル等は行って
  いない。調べ方は、通常の操作で保存されたファイル同士の差分を
  観察し、値を書き換えたファイルを CSP で開き直して結果を確認する、
  ファイルに対するブラックボックス観察である(§13)。
- ユーザーが作成した `.clip` ファイルは、そのユーザー自身の
  著作物である。利用者は、自らの著作物であるファイルを自ら
  読み書きする権利があると信じている。
- 本書に基づくファイルの読み書きは、著作物の表現を変えるものでは
  なく、電子計算機で利用するための機械的・形式的な変換である。
  これは著作権法第47条の4第1項が定める「電子計算機における
  著作物の利用に付随する利用」に当たると考えている。
- 本書の目的の一つは、将来、ソフトウェアの提供終了や環境の変化に
  よって自分の作品データが読み書きできなくなる危険性への備え
  として、形式の知識を記録しておくことにある。

### 確度の表記

| 表記 | 意味 |
|---|---|
| **[確定]** | ファイルを書き換えて CSP で開き直し、表示・動作で確認した |
| **[観測]** | 複数のサンプルで、値と操作の対応を確認した(書き換えによる確認はしていない) |
| **[外部]** | 既存の実装・調査による情報。手元のサンプルと矛盾しないことは確認した |
| **[推定]** | 未検証の解釈 |

参考にした既存実装: [clipfile-rs](https://github.com/Aodaruma/clipfile-rs)、[clip_to_psd](https://github.com/dobrokot/clip_to_psd)、[cliputils](https://github.com/rasensuihei/cliputils)

---

## 目次

1. [全体像](#1-全体像)
2. [コンテナ (CSFCHUNK)](#2-コンテナ-csfchunk)
3. [埋め込み SQLite の共通規則](#3-埋め込み-sqlite-の共通規則)
4. [文書・キャンバス](#4-文書キャンバス)
5. [レイヤー](#5-レイヤー)
6. [ビットマップ (Mipmap / Offscreen / BlockData)](#6-ビットマップ-mipmap--offscreen--blockdata)
7. [ベクターレイヤー](#7-ベクターレイヤー)
8. [ブラシスタイル](#8-ブラシスタイル)
9. [描画モデル (ベクター線の再現)](#9-描画モデル-ベクター線の再現)
10. [描画キャッシュと CSP の再描画](#10-描画キャッシュと-csp-の再描画)
11. [書き込み時の規則 (チェックリスト)](#11-書き込み時の規則-チェックリスト)
12. [実装レシピ](#12-実装レシピ)
13. [検証コーパスと実験方法](#13-検証コーパスと実験方法)
14. [未解明事項](#14-未解明事項)

---

## 1. 全体像

```
.clip
├─ CSFCHUNK ヘッダ
├─ CHNKHead ─────────── CHNKSQLi の絶対位置
├─ CHNKExta × N ─────── 外部データ本体 (id "extrnlid…")
│    ├ ベクター本体            ← VectorObjectList.VectorData
│    ├ ビットマップタイル群     ← Offscreen.BlockData
│    └ その他 (タイムライン, タイムラプス, 3D, 音声 …)
├─ CHNKSQLi ─────────── SQLite 3 DB (文書のすべてのメタデータ)
└─ CHNKFoot
```

SQLite 内でのベクターレイヤーまわりの参照関係:

```
Canvas.CanvasRootFolder ─► Layer(root folder)
                              │ LayerFirstChildIndex (一番下の子)
                              ▼
                           Layer ─LayerNextIndex─► Layer ─► … (下から上へ)
                              │
     ┌────────────────────────┼─────────────────────────────┐
     │ LayerRenderMipmap      │ VectorNormalStrokeIndex      │ LayerRenderThumbnail
     ▼                        ▼                              ▼
   Mipmap                VectorObjectList               LayerThumbnail
     │ BaseMipmapInfo         │ VectorData (40B id)          │ ThumbnailOffscreen
     ▼                        ▼                              ▼
   MipmapInfo(100%)──Next──► MipmapInfo(50%) ─► …          Offscreen
     │ Offscreen              CHNKExta: ストローク列
     ▼
   Offscreen ── BlockData (40B id) ──► CHNKExta: タイル群 (ベクターレイヤーでは実体なし)

ストローク.brush_id ─► BrushStyle.MainId ─Size/OpacityEffector─► BrushEffectorGraphData.MainId
BrushStyleManager.FirstBrushStyle / FirstGraphData ─► 各連結リストの先頭
ExternalChunk(ExternalID, Offset) : 外部 id → CHNKExta の絶対ファイル位置
```

---

## 2. コンテナ (CSFCHUNK)

### 2.1 ルートヘッダ (24 バイト) [確定]

| オフセット | 型 | 内容 |
|---:|---|---|
| 0 | char[8] | `"CSFCHUNK"` |
| 8 | u64 | ファイル全体のバイト数 |
| 16 | u64 | 最初のチャンクの位置。常に `24` |

### 2.2 チャンク [確定]

```
char[8] tag ; u64 payload_len ; payload[payload_len]
```
次のチャンクは `pos + 16 + payload_len` から始まります。パディングはありません。並びは `CHNKHead`、`CHNKExta` × 0 個以上、`CHNKSQLi`、`CHNKFoot`(payload 0)の順です。

### 2.3 CHNKHead (payload 40 バイト) [確定]

| オフセット | 型 | 内容 |
|---:|---|---|
| 0 | u64 | `0x100` |
| 8 | u64 | `CHNKSQLi` チャンク先頭(タグの位置)の**絶対**オフセット |
| 16 | u64 | `16` (識別子の長さ) |
| 24 | byte[16] | ファイル識別子(UUID 形式のランダム値) |

### 2.4 CHNKExta [確定]

| オフセット | 型 | 内容 |
|---:|---|---|
| 0 | u64 | `40` (id の長さ) |
| 8 | char[40] | id: `"extrnlid"` + 大文字 16 進 32 桁 |
| 48 | u64 | 本体の長さ(= payload_len − 56) |
| 56 | byte[] | 本体(用途ごとの形式。§6.3, §7.2) |

### 2.5 CHNKSQLi [確定]

payload は SQLite 3 のデータベースファイルそのもの(`"SQLite format 3\0"` で始まる)です。一時ファイルに書き出せば普通の SQLite として開けます。手元のサンプルではページサイズ 4096 でした。

### 2.6 書き込み時の整合性 [確定]

チャンクの長さが1つでも変わると、次の3つがずれます。すべて計算し直してください。
1. ルートヘッダのファイルサイズ
2. `CHNKHead` の SQLi 位置
3. SQLite `ExternalChunk.Offset`(各 CHNKExta のタグ位置)

`ExternalChunk.Offset` を書き換えると SQLite の大きさが変わることがあります。そのため、DB を確定させてから位置を計算し、必要ならもう一度計算し直します(`clipvec/container.py: ClipFile.serialize`)。変更の無いファイルを解析して書き出すと、元のバイト列と完全に一致します。

---

## 3. 埋め込み SQLite の共通規則

### 3.1 行の識別子

* `_PW_ID INTEGER PRIMARY KEY AUTOINCREMENT` … SQLite の行 ID です。他の行からは参照されません
* `MainId` … CSP 内部の ID です。他のテーブルからの参照はすべてこの列に向きます
* `CanvasId` … 所属するキャンバス(通常 1)
* 連結リストは `NextIndex` / `LayerNextIndex` などで表します。値 `0` が終端です

### 3.2 `ElemScheme` — ID の最大値 [確定]

`ElemScheme(TableName, ElemType, MaxIndex)` はテーブルごとに、**これまで使われた MainId の最大値**を持っています。行を削除しても値は下がりません(空テンプレートは Offscreen の行が 10 個なのに `MaxIndex = 17`)。
CSP はこの値を元に次の ID を決めます。新しい行を足すときは `MainId = max(MaxIndex, max(MainId)) + 1` とし、`MaxIndex` も更新してください。これを怠ると、後で CSP が追加する行と ID がぶつかる恐れがあります。

### 3.3 `ParamScheme` — 列の定義 [観測]

`ParamScheme(TableName, LabelName, DataType, Flag, OwnerType, LockType, …)` は各テーブルの列の定義です。**まだ存在しないテーブルの列まで**あらかじめ登録されているので、テーブルを作っても編集は不要でした。

### 3.4 外部データの索引 [確定]

| テーブル | 内容 |
|---|---|
| `ExternalChunk(ExternalID TEXT, Offset INTEGER)` | id(**TEXT** で保存)→ CHNKExta の絶対位置 |
| `ExternalTableAndColumnName(TableName, ColumnName)` | 外部 id を持ちうる列の一覧: `Offscreen.BlockData`, `VectorObjectList.VectorData`, `Canvas3DModelBank.BankData`, `Canvas3DModelLoader.ModelData`, `Track.TrackActionMixer(2)`, `CanvasItemBinary.ItemBinaryData`, `Manager3DOd.SceneData`, `ModelData3D.Layer3DModelData`, `TimeLapseBlob.BlobData` |
| `RemovedExternal(ExternalID)` | 不要になった外部 id の記録 |

* 参照する側の列(`VectorData`, `BlockData`)は 40 バイトの **BLOB** です。`ExternalChunk.ExternalID` は **TEXT** なので、比較するときは型を揃えてください
* 参照先の id が `ExternalChunk` にも CHNKExta にも無いのは**正常な状態**です。ビットマップなら「中身が空」、ベクターの描画キャッシュなら「保存されていない」を意味します(§6, §10)
* `UPDATE OF VectorData` / `DELETE` で古い id を `RemovedExternal` に書き込むトリガー(`ExternalUpdateOf…On…`)が付いていることがあります。**付いているかどうかは保存ごとに変わる**ので(同じ環境で 0 個 / 2 個 / 4 個を観測)、あてにしないでください [観測]

### 3.5 スキーマは可変です [観測]

テーブルは必要になるまで作られません。ストロークが一本も無い文書には `VectorObjectList`, `BrushStyle`, `BrushEffectorGraphData` がありません。列も版によって増えます(clipfile-rs の報告では `Layer` が 64 列と 83 列)。列は `PRAGMA table_info` で名前ごとに調べ、知らない列は触らずに残してください。本書で使う CREATE 文は `clipvec/schema.py` に収録しています。

---

## 4. 文書・キャンバス

### 4.1 `Canvas` [確定]

| 列 | 内容 |
|---|---|
| `CanvasWidth`, `CanvasHeight` | REAL、px |
| `CanvasResolution` | REAL、dpi |
| `CanvasUnit` | 0 = px |
| `CanvasChannelBytes` | 1 (8bit/ch) |
| `CanvasDefaultChannelOrder` | 33 |
| `CanvasRootFolder` | ルートフォルダの `Layer.MainId` |
| `CanvasCurrentLayer` | 選択中のレイヤーの `Layer.MainId` |
| `BrushStyleManager` | `BrushStyleManager.MainId` |

### 4.2 `Project` [観測]

`ProjectInternalVersion`(`"1.1.0"`)、`DefaultPageWidth/Height/Resolution`(新規作成時の設定。キャンバスサイズを変えるときはこれも合わせる)、`DefaultPageSettingType`(`"Illust"` など)。

### 4.3 `CanvasPreview` [確定]

`ImageType = 1`、`ImageWidth`, `ImageHeight`、`ImageData` = PNG。ファイルブラウザで使われる縮小画像です。書き換えた場合も、CSP は開く・表示する・保存するのに問題ありませんでした。

---

## 5. レイヤー

### 5.1 階層と順序 [確定]

* ルートは `Canvas.CanvasRootFolder` が指すフォルダ(`LayerType = 256`, `LayerFolder = 1`)です
* フォルダの `LayerFirstChildIndex` は**一番下**の子を指します。`LayerNextIndex` を辿ると**上**へ進みます(CSP のレイヤーパレットでは逆順に並びます)
* 「レイヤー R の真上に N を挿入する」には `N.next = R.next; R.next = N` とします

### 5.2 主な列

| 列 | 内容 | 確度 |
|---|---|---|
| `LayerName` | TEXT (UTF-8) | 確定 |
| `LayerType` | 0 = ベクター/テキストなど、1 = ラスター、256 = ルートフォルダ、4098 = 色調補正 [外部]、512 = 2D カメラ [外部]、1584 = 用紙 [外部] | 観測 |
| `LayerFolder` | 1 = フォルダ | 観測 |
| `LayerVisibility` | 1 = 表示 | 確定 |
| `LayerOpacity` | 256 = 100% | 観測 |
| `LayerComposite` | 合成モード。0 = 通常 | 観測 |
| `LayerSelect` | 選択状態 | 観測 |
| `LayerUuid` | TEXT。`'6502eb0d97-e54a-d794-1f36-0b52efd09b'` のように 10-4-4-4-10 桁の 16 進 | 観測 |
| `LayerRenderMipmap` | → `Mipmap.MainId`(描画キャッシュ) | 確定 |
| `LayerRenderThumbnail` | → `LayerThumbnail.MainId` | 観測 |
| `LayerLayerMaskMipmap` | → マスク用の `Mipmap` [外部] | |
| `DrawToRenderOffscreenType` | **10 = ベクターレイヤー**(ラスターは NULL) | 観測 |
| `VectorNormalStrokeIndex` | **→ `VectorObjectList.MainId`**(このレイヤーで描くストローク列)。空のベクターレイヤーでは 0 | **確定** |
| `VectorNormalFillIndex`, `VectorNormalBalloonIndex` | 塗り・フキダシ用と思われる [推定] | |
| `MixSubColorForEveryPlot` | ベクターレイヤーでは 1 | 観測 |

**ベクターレイヤーの見分け方**: `LayerType = 0` かつフォルダでない、かつ `DrawToRenderOffscreenType = 10`(テキストレイヤーは `TextLayerType` 列を持つ [外部])。または `VectorObjectList` から参照されているレイヤー。

---

## 6. ビットマップ (Mipmap / Offscreen / BlockData)

### 6.1 テーブルの関係 [確定]

```
Layer.LayerRenderMipmap → Mipmap(MainId, LayerId, MipmapCount=4, BaseMipmapInfo)
Mipmap.BaseMipmapInfo   → MipmapInfo(ThisScale=100, Offscreen, NextIndex)
                             → MipmapInfo(50) → MipmapInfo(25) → MipmapInfo(12.5) → 0
MipmapInfo.Offscreen    → Offscreen(MainId, LayerId, Attribute BLOB, BlockData BLOB=外部id)
Layer.LayerRenderThumbnail → LayerThumbnail(…, ThumbnailOffscreen → Offscreen 512×512)
```
縮小段の大きさは `ceil(キャンバス × ThisScale/100)` です(1000px → 1000/500/250/125)。サムネイルは常に 512×512 でした。
`LayerThumbnail` には他に `ThumbnailCanvasWidth/Height`(キャンバスサイズ)や、`Thumbnail*NeedRefresh` といった更新管理用のカウンタがあります。

### 6.2 `Offscreen.Attribute` BLOB [確定]

```
u32 16                   ヘッダ長
u32 len_Parameter        "Parameter" 節の長さ (102)
u32 len_InitColor        "InitColor" 節の長さ (42)
u32 len_BlockSize        "BlockSize" 節の長さ (= 34 + 4 × タイル数)
節 := u32 文字数, UTF-16BE 名前, データ   (名前が 18 バイトなので以降は 4 バイト境界に揃わない)
"Parameter": u32 width, height, grid_w = ceil(w/256), grid_h = ceil(h/256),
             u32 × 16 = 33, 1, 4, 5, 65536, 4, 1024, 1, 256, 65536, 256, 256, 8, 8, 0, 0
"InitColor": u32 × 5 = 20, 0, 0, 0, 4        (初期色。clip_to_psd は 58 バイト版に RGBA を持つ例を記載)
"BlockSize": u32 12, u32 タイル数, u32 4, u32 × タイル数 (各タイルのブロックレコードの長さ。空 = 104)
```
* Parameter の 16 個の整数は、ルートフォルダ・ラスター・ベクター・サムネイルのすべてで同じ値でした。チャンネル並び(33 = `CanvasDefaultChannelOrder`)、1 行あたりのバイト数(1024 = 256×4)、タイルの幅と高さ(256)などと解釈できます [推定]
* **BlockSize は目安にすぎません**。実体を保存しないベクターレイヤーやルートの合成画像でも、CSP がメモリ上で描画した時点のサイズが残っています。読むときは BlockData 本体の長さだけを信用してください [観測]

### 6.3 BlockData 本体 (CHNKExta) [確定] [外部]

タイルごとのブロックレコードを `grid_w × grid_h` 個並べ(行優先、左上から)、その後ろに Status 表と CheckSum 表が付きます。

```
ブロックレコード:
  u32 レコード長 (このフィールドから End ラベルの末尾まで。空タイル = 104)
  u32 19, UTF-16BE "BlockDataBeginChunk"
  u32 タイル番号 (0 始まり)
  byte[12] タイル属性: u16 チャンネル数 (5 = カラー, 1 = マスク), u16 0, u32 256, u32 256
  u32 データ有無 (0 / 1)
  [データ有りの場合]
    u32 外側の長さ (= 内側 + 4)
    u32 LE 内側の長さ (zlib データのバイト数)
    byte[] zlib (先頭 78 01)
  u32 17, UTF-16BE "BlockDataEndChunk"
u32 11, UTF-16BE "BlockStatus",   u32 12, u32 タイル数, u32 4, u32 × タイル数 (手元ではすべて 1)
u32 13, UTF-16BE "BlockCheckSum", u32 12, u32 タイル数, u32 4, u32 × タイル数
```
* **チェックサム** = `Adler-32( u32 LE 内側の長さ ‖ zlib データ )`。空タイルは 0 です(手元の全タイルで一致) [確定]
* **画素**(5 チャンネル、展開後 5 × 65536 バイト): 先頭 65536 バイトが α、続く 65536 × 4 バイトが画素ごとの `B, G, R, x` です。α は**乗算済みではありません**(α = 6 の画素に 1A80F0 がそのまま入っていた) [確定]
* 参照先の外部 id が存在しない Offscreen は、`InitColor` で塗りつぶした空のタイルとして扱います [外部]

---

## 7. ベクターレイヤー

### 7.1 `VectorObjectList` [確定]

```sql
CREATE TABLE VectorObjectList(_PW_ID INTEGER PRIMARY KEY AUTOINCREMENT, MainId INTEGER, CanvasId INTEGER,
                              LayerId INTEGER, VectorData BLOB)   -- + INDEX idxVectorObjectList(MainId)
```
ベクターレイヤー 1 枚につき 1 行です(最初の線を描いたときに作られます)。`Layer.VectorNormalStrokeIndex` がこの行の `MainId` を指し、`VectorData` の外部 id がストローク列の本体を指します。

### 7.2 ベクター本体 = ストロークレコードの連結

```
body := stroke*
stroke := header[header_size] point[point_count] (各 point_size バイト)
```
全体を読み進めるのに使うのは、各ヘッダの先頭 20 バイト(サイズ類と点数)だけです。

### 7.3 ストロークヘッダ

**共通部分 (0–75)**

| オフセット | 型 | 内容 | 確度 |
|---:|---|---|---|
| 0 | u32 | header_size: 88(通常の線。CSP 1.13 の出力)/ 92(新しい版の通常の線 [外部])/ 76(定規 [外部])/ 96(コマ枠 [外部]) | 観測 |
| 4 | u32 | 72(92 バイト版では 76) | 観測 |
| 8 | u32 | point_size: 88 / 104(2 次ベジェ)/ 120(3 次ベジェ) | 確定 |
| 12 | u32 | 既定の点サイズ 88 | 観測 |
| 16 | u32 | point_count | 確定 |
| 20 | u32 | flags(§7.4) | 確定 |
| 24 | i32 ×4 | バウンディングボックス left, top, right, bottom(キャンバス px。すべての点ボックスの和) | 観測 |
| 40 | u32 ×3 | メインカラー R, G, B。8 bit 値を 4 回繰り返した値(`0x1A` → `0x1A1A1A1A`)。上位 8 bit を 8 bit 色として使う | 確定 |
| 52 | u32 ×3 | サブカラー(同じ形式) | 観測 |
| 64 | f64 | ストロークの不透明度 0..1(ツールの「不透明度」) | 確定 |
| 72 | u32 | ブラシスタイル id → `BrushStyle.MainId` | 確定 |

**88 バイト版(76–87)**

| オフセット | 型 | 内容 | 確度 |
|---:|---|---|---|
| 76 | f64 | ブラシ半径(キャンバス px)= ブラシサイズ[mm] / 2 × dpi / 25.4 | 確定 |
| 84 | u32 | ストロークのシード値。線を描くたびにほぼミリ秒刻みで増える。点 @80 の既定値 | 観測 |

**92 バイト版** [外部]: 76 = 未知の u32、80 = f64 半径、88 = u32 シード。
**96 バイト版(コマ枠)** [外部]: 76 = u32 枠ブラシ id、80 = u32 塗りスタイル id、84 = f64 半径。

### 7.4 ストロークのフラグ

| ビット | 意味 | 根拠 |
|---|---|---|
| `0x0001` | 通常の線 | 全サンプル |
| `0x0004` | 定規 [外部] | |
| `0x0008` | コマ枠 [外部] | |
| `0x0010` | 直線補間 | 直線ツール: `0x11`、折れ線ツール: `0x1011` |
| `0x0020` | 2 次ベジェ(point_size 104) | 書き込みで確定 |
| `0x0040` | 3 次ベジェ(point_size 120) | 書き込みで確定 |
| `0x0080` | スプライン | フリーハンド: `0x2081`、楕円: `0x181` |
| `0x0100` | 閉じた線 | 楕円、書き込みで確定 |
| `0x1000` | 折れ線ツールで付く(点に角の追加データ) | 観測 |
| `0x2000` | フリーハンド入力 | 観測 |

`flags & 0x0D` が線の種類、`flags & 0xF0` が曲線の種類です。
折れ線ツールで描くと、最後の点が重複して保存されることがありました [観測]。

### 7.5 点レコード (88 バイト + 制御点)

| オフセット | 型 | 内容 | 確度 |
|---:|---|---|---|
| 0 | f64 | x(キャンバス px、右向きが正) | 確定 |
| 8 | f64 | y(キャンバス px、**下向き**が正) | 確定 |
| 16 | i32 ×4 | この点から次の点までの区間のバウンディングボックス。最後の点は自分自身のボックス | 観測 |
| 32 | u32 | 点フラグ: `0x1` = **角**(スプラインの接線がここで切れる。フリーハンドの終点にも立つ)、`0x2` = 折れ線の角データあり | 確定 / 観測 |
| **36** | f32 | **筆圧** 0..1(§9.1) | **確定** |
| 40 | f32 | 傾き 0..1。1 = ペンが垂直、およそ 1 − 傾き角/90°(入力 45° → 0.5、60° → 0.333) | 観測 |
| 44 | f32 | 描画速度(正規化)。ゆっくり描くと小さい | 観測 |
| 48 | f32 | 未知。常に 0(ペンの回転を注入しても変化なし) | 観測 |
| 52 | f32 | 未知。常に 1。0.3 に書き換えても G ペンの描画は変わらなかった | 確定(効果なし) |
| **56** | f32 | **線幅の倍率**。描画幅に掛かる | **確定** |
| **60** | f32 | **不透明度の倍率**。描画時の不透明度に掛かる | **確定** |
| 64 | f32 ×3 | 通常は 0。折れ線の角(点フラグ `0x2`)で値が入る(例: 1.818, 0.160, 0.987) | 観測 |
| 76 | f32 | 乱数値 0..1。座標から決まる(同じ座標なら同じ値)。始点は 0 | 観測 |
| 80 | u32 | 点のシード。普段はヘッダのシードと同じ値。ランダム系エフェクター付きのブラシでは点ごとに違う | 観測 |
| 84 | u32 | 0 | 観測 |
| 88 | f64 ×2 | (2 次)制御点 Q | 確定 |
| 88, 104 | f64 ×2 ×2 | (3 次)制御点 C1, C2 | 確定 |

**制御点はその点から始まる区間**(i → i+1。閉じた線では最後の点 → 最初の点)に属します [確定]。

### 7.6 CSP が保存する点の間隔 [観測]

フリーハンドの線は、CSP が点を間引いて保存します。直線状の部分は数点に減り(800px の直線が 5〜7 点)、曲がった部分は 8〜25px ごとに点が残ります。手ブレ補正の設定は、保存される座標そのものに反映されます。

---

## 8. ブラシスタイル

### 8.1 `BrushStyle` [確定]

ストロークを描いた時点のブラシ設定の複製です。設定を変えて描くと、新しい行が追加されます(`t03`: 筆圧の最小値を 50% にして描くと id 2 が増えた)。列は 66 個あります(`PenRadius`, `SizeEffector`, `OpacityEffector`, `FlowBase`/`FlowEffector`, `AntiAlias`, `Hardness`, `Interval*`, `Thickness*`, `Rotation*`, `Texture*`, `WaterColor*`, `MixColor*`, `Spray*`, `WaterEdge*` など)。
行は `NextIndex` で繋がり、先頭は `BrushStyleManager.FirstBrushStyle` です。

### 8.2 エフェクター BLOB [確定]

`SizeEffector`, `OpacityEffector`, `FlowEffector`, `ThicknessEffector`, … で共通の形式です(`t08` で、ブラシサイズの影響元設定のチェックを1項目ずつ変えて解明)。

```
u32 flags : bit0 = 1 (常に), 0x10 = 筆圧, 0x20 = 傾き, 0x40 = 速度, 0x80 = ランダム
if 0x10: f32 最小値, u32 グラフ id
if 0x20: f32 最小値, u32 グラフ id, f32 最大値
if 0x40: f32 最小値
if 0x80: f32 最小値
```

| UI の設定 | SizeEffector |
|---|---|
| G ペンの初期値(筆圧 最小 0% + 傾き 100〜150%) | `00000031 00000000 00000001 3f800000 00000002 3fc00000` |
| 筆圧のみ、最小 50% | `00000031 3f000000 00000001 …` |
| 筆圧のみ、最小 0% | `00000011 00000000 00000001` |
| 筆圧 + 速度 最小 30% | `00000051 00000000 00000001 3e999999` |
| 筆圧 + ランダム 最小 40% | `00000091 00000000 00000001 3ecccccc` |
| すべてオフ | `00000001` |

`OpacityEffector = 00000001`(影響元なし)が G ペンの初期値です。

### 8.3 `BrushEffectorGraphData` [確定]

`(MainId, NextIndex, ControlNumber, ControlDataSize = 16, ControlPoints)`。`ControlPoints` は `ControlNumber × (f64 x, f64 y)` です。
* id 1: `(0,0) (1,1)` … 線形(筆圧グラフの初期値)
* id 2: `(0,1) (0.245,0.927) (0.391,0.4) (0.655,0) (1,0)` … 傾きグラフの初期値(x: 0 = 寝かせた状態 … 1 = 垂直)

行は `NextIndex` で繋がり、先頭は `BrushStyleManager.FirstGraphData` です。

---

## 9. 描画モデル (ベクター線の再現)

### 9.1 筆圧の意味 [確定]

* 点 @36 は「タブレットの生の入力に、CSP 全体の筆圧カーブ(筆圧検知レベルの調節)を掛けた値」です。合成ペンの入力 0.1 → 0.254、0.25 → 0.515、0.5 以上 → 1.0 でした(`t02`)
* ブラシ側の筆圧設定を変えて描いても @36 は同じ値です(`t03`)。ブラシの設定は**描画時に**掛かります
* @36 を 1.0 に書き換えると、線が太く描画されました(`m01_v3`)

### 9.2 線幅と不透明度 [確定: 筆圧と倍率 / 推定: 傾き・速度・ランダム]

```
描画半径(px) = ヘッダ半径@76 × E_size(点) × 線幅倍率@56
描画不透明度 = ストローク不透明度@64 × E_opacity(点) × 不透明度倍率@60   (× レイヤー不透明度)

E(点) = Π(有効な項目)
  筆圧    : min + (1 − min) · graph(筆圧@36)
  傾き    : min + (max − min) · graph(傾き@40)
  速度    : min + (1 − min) · (1 − 速度@44)      [推定]
  ランダム : min + (1 − min) · 乱数@76           [推定]
graph() : BrushEffectorGraphData の折れ線補間 (CSP の UI では滑らかな曲線で描かれる)
```
ブラシの形状、テクスチャ、アンチエイリアス、水彩などは、`BrushStyle` の他の列が描画時に効きます(本書の範囲外)。

### 9.3 中心線の補間 [確定]

| 曲線の種類 | 区間 i → i+1 |
|---|---|
| スプライン `0x80` | 保存された点を通る**一様 Catmull-Rom**: `0.5·(2P1 + (−P0+P2)t + (2P0−5P1+4P2−P3)t² + (−P0+3P1−3P2+P3)t³)` |
| 直線 `0x10` | 線形補間 |
| 2 次 `0x20` | `(1−t)²P_i + 2(1−t)t·Q_i + t²P_{i+1}` |
| 3 次 `0x40` | `(1−t)³P_i + 3(1−t)²t·C1_i + 3(1−t)t²·C2_i + t³P_{i+1}` |

* スプラインの端点と角(点フラグ `0x1`)では、存在しない前後の点の代わりに、隣の点を反対側へ鏡映した仮想点を使います(始点なら `P0 = 2·P1 − P2`)
* 閉じた線では、最後の点から最初の点へ繋がる区間が加わり、前後の点も一周して取ります
* 根拠: 疎な点で描いた線を画面から取り込み、描画された中心線と比べました(`tools/splinefit.py`)。平均誤差は、一様 Catmull-Rom 0.017px、線形 0.05px、一様 B-スプライン 0.07px、centripetal 0.12px、chordal 0.47px でした。ベジェは、自前でサンプリングした細い線を重ねて描かせ、一致することを確認しました
* 筆圧などの点ごとの値は、区間内で線形に補間します [推定]
* 変換のメモ: 一様 Catmull-Rom は 3 次ベジェに正確に変換できます(`C1 = P1 + (P2 − P0)/6`, `C2 = P2 − (P3 − P1)/6`)。2 次は `C1 = P0 + ⅔(Q − P0)`, `C2 = P1 + ⅔(Q − P1)` で 3 次になります

### 9.4 バウンディングボックス [観測]

CSP が保存する値は「区間の曲線に、半径より少し大きい余白を加えたもの」です(半径 1.42 → 余白 3、5.67 → 9、14.17 → 23)。正確な式は分かっていませんが、**大きめの値を書いても CSP は正しく描画し、そのまま保存し直します**。`clipvec` の余白は `半径 × max(1, 線幅倍率) × 1.6 + 2` です(傾きによる最大 150% の拡大も収まる)。

---

## 10. 描画キャッシュと CSP の再描画

**書き込み機能を作るうえで最も重要な点です。**

1. **CSP はベクターレイヤーとフォルダの合成結果の画素を保存しません** [観測]。`Offscreen.BlockData` が指す外部 id は CHNKExta にも `ExternalChunk` にもありません(サムネイルだけは保存されます)。開くたびにベクターデータから描き直します
2. ただし CSP は**Offscreen の外部 id をキーにして、描画結果をメモリ上にキャッシュ**しています。ストロークだけを書き換えたファイルを同じセッションで開くと、古い絵が表示されます [確定: `m01_fields`, `m01_v2`]
3. 対策として、書き換えたベクターレイヤーと、その上の各フォルダの**すべての Offscreen の `BlockData` を、新しい(どこにも存在しない)外部 id に差し替えます**。これで CSP は描き直します [確定: `m01_v3`]
4. `LayerRenderMipmap = 0` にすると、そのレイヤーは何も描かれなくなります [確定: `m01_v1`]
5. `DrawToRenderOffscreenType = 0` にしても、キャッシュは消えませんでした [確定: `m01_v2`]

---

## 11. 書き込み時の規則 (チェックリスト)

ストロークを編集する場合:
- [ ] ベクター本体を書き直す(未知のフィールドは元のバイトを残す)
- [ ] 点のボックスとストロークのボックスを計算し直す(大きめで構わない)
- [ ] 書き換えたレイヤーと、その上のフォルダの Offscreen `BlockData` を新しい id に差し替える(§10)
- [ ] コンテナの位置情報を計算し直す(§2.6)

ベクターレイヤーを追加する場合:
- [ ] 既存のベクターレイヤーの `Layer`, `Mipmap`, `MipmapInfo`(連結リストも含めて)、`Offscreen`(新しい id)、`LayerThumbnail` の各行を複製し、`MainId` と相互参照を付け替える
- [ ] `LayerUuid` を新しく作る、`LayerSelect = 0`
- [ ] 兄弟の連結リストに挿入する(§5.1)
- [ ] `VectorObjectList` の行を作り、**`Layer.VectorNormalStrokeIndex` にその MainId を入れる**(複製元の値のままだと、元のレイヤーの線が描かれてしまう)[確定]
- [ ] ベクター本体を CHNKExta として追加し、`ExternalChunk` に行を足す
- [ ] 追加したすべてのテーブルで `ElemScheme.MaxIndex` を更新する(§3.2)

テーブルが無い場合(ストロークの無い文書):
- [ ] `VectorObjectList`, `BrushStyle`, `BrushEffectorGraphData` を CREATE する(インデックスも作る。トリガーは任意)
- [ ] `BrushStyle` と `BrushEffectorGraphData` の行を作り、`BrushStyleManager.FirstBrushStyle` / `FirstGraphData` の連結リストに繋ぐ

キャンバスサイズを変える場合(空の文書のみ):
- [ ] `Canvas`、`Project.DefaultPage*`、`LayerThumbnail.ThumbnailCanvasWidth/Height`
- [ ] サムネイル以外のすべての Offscreen について、`Attribute` の Parameter(幅・高さ・タイル数)と BlockSize 表を作り直し、BlockData を新しい id にする(§6.2)
- [ ] `CanvasPreview` の PNG

これらを守ったファイルは、CSP で開いて描画し、さらに編集(描き足し・レイヤー追加)して保存しても問題ありませんでした。CSP は既存の ID と `MaxIndex` を尊重して、新しい ID を割り当てました(`w04_edited_in_csp`)。

---

## 12. 実装レシピ

### 12.1 全ストロークを読む

```python
import sqlite3, struct, tempfile

data = open(path, "rb").read()
chunks, pos = [], 24
while pos < len(data):
    tag, n = data[pos:pos+8], struct.unpack_from(">Q", data, pos+8)[0]
    chunks.append((tag, pos, data[pos+16:pos+16+n])); pos += 16 + n
ext = {}
for tag, _, p in chunks:
    if tag == b"CHNKExta":
        idlen = struct.unpack_from(">Q", p, 0)[0]
        blen = struct.unpack_from(">Q", p, 8 + idlen)[0]
        ext[p[8:8+idlen]] = p[16+idlen:16+idlen+blen]
db_path = tempfile.mktemp(); open(db_path, "wb").write(next(p for t, _, p in chunks if t == b"CHNKSQLi"))
con = sqlite3.connect(db_path)
for layer_id, vid in con.execute("select LayerId, VectorData from VectorObjectList"):
    body, off = ext.get(bytes(vid), b""), 0
    while off < len(body):
        hs, _, psz, _, n = struct.unpack_from(">5I", body, off)
        radius = struct.unpack_from(">d", body, off + 76)[0] if hs == 88 else struct.unpack_from(">d", body, off + 80)[0]
        for k in range(n):
            q = off + hs + k * psz
            x, y = struct.unpack_from(">dd", body, q)
            pressure, tilt = struct.unpack_from(">ff", body, q + 36)
            width_f, opacity_f = struct.unpack_from(">ff", body, q + 56)
        off += hs + n * psz
```

### 12.2 本リポジトリの API

| やりたいこと | API |
|---|---|
| コンテナの読み書き(バイト単位で往復一致) | `clipvec.container.ClipFile` |
| ストローク・点の変換(未知バイトは保持) | `clipvec.vector.decode / encode`, `Stroke`, `Point`, `Stroke.sample()` |
| レイヤー・ブラシ・新規文書・保存(§11 の規則を内部で処理) | `clipvec.document.ClipDocument` |
| エフェクターの解析と計算 | `clipvec.brush` |
| ラスターのタイル展開 | `clipvec.raster` |
| JSON / SVG | `clipvec.interchange`, `clipvec.svg`, `python -m clipvec …` |

---

## 13. 検証コーパスと実験方法

### 13.1 方法

* `tools/gui.py`: Win32 の `InjectSyntheticPointerInput` で合成ペン(PT_PEN)を作り、筆圧 0..1024 と tiltX/Y を与えて CSP に線を描かせます。CSP の「タブレットサービス」が TabletPC のとき、筆圧・傾きが CSP に届きます(ペンの回転は無視されました)
* 保存・読み込みは、ファイルダイアログにフルパスを入力して行いました(`tools/h.py`)
* 差分は `tools/vdump.py`(ベクター本体)と `tools/dbdiff.py`(SQLite)で取りました
* 書き換えたファイルを CSP で開いて画面を取り込み、描画を確かめました

### 13.2 サンプル (`samples/`)

| ファイル | 内容 | 主に確かめたこと |
|---|---|---|
| `t01_pressure_ramp` | 筆圧 0→1 の線とマウスの線 | 点の構造、フリーハンドのフラグ |
| `t02_const_pressure` | 一定筆圧 0.1/0.25/0.5/0.75/1 | @36 = 筆圧、@76 = 座標で決まる乱数 |
| `t03_min50` | ブラシの筆圧最小値を 50% に | BrushStyle の行の追加、@36 がブラシ設定に依存しないこと |
| `t04_tilt` | 傾き・回転・低速 | @40 = 傾き、@44 = 速度 |
| `t05_color_opacity_size` | 色、不透明度 50%、サイズ 10 | 色の形式、@64、@76 = 半径 |
| `t06_curves` | サイン波、円、ジグザグ | 角のフラグ |
| `t07_spline` | 疎な点の線 | 補間の方式(Catmull-Rom) |
| `t08_effectors` | 速度・ランダムのエフェクター | エフェクター BLOB、点ごとのシード |
| `t09_figures` | 直線・楕円・折れ線 | フラグ `0x11`/`0x181`/`0x1011` |
| `t10_with_raster` | ラスターとベクターの混在 | タイル・チェックサム・画素の並び |
| `m01_fields`, `m01_v1..v3` | 値の書き換え、キャッシュの無効化 | @36/@56/@60 の効果、§10 |
| `w01_synthetic`(+ `_resaved`) | ゼロから作ったストローク | 書き込み。CSP で保存し直してもバイト単位で同じ |
| `w02_newlayers`(+ `_resaved`) | レイヤーの追加 | `VectorNormalStrokeIndex`、順序 |
| `w03_bezier` | 2 次・3 次ベジェ | 制御点の位置 |
| `w04_new_1920`, `w04_edited_in_csp` | 新規作成(サイズ変更)と、CSP での追加編集 | §6.2, §3.2 |
| `template_empty` | CSP で作った空のベクター文書 | テーブルが無い状態 |
| `b01_from_blender`, `b02_raster_kept` | Blender 経由の書き出し | 実際の往復、ラスターが残ること |

`python tools/selftest.py` で、全サンプルについて「コンテナ・ベクター本体・JSON を往復して内容が変わらないこと」と「新規作成したファイルの整合性」を確認できます。

---

## 14. 未解明事項

| 項目 | 現状 | 次の調べ方 |
|---|---|---|
| 点 @48, @52 | 常に 0 / 1。@52 を書き換えても G ペンの描画は変わらない | 厚さ(Thickness)や回転を使うブラシで描き比べる |
| 折れ線の角データ(@64–72、点フラグ `0x2`、ストロークフラグ `0x1000`) | 元のバイトを保持している | 角の形(丸・尖り)の設定を変えて描き比べる |
| 速度・ランダムの曲線、傾きグラフの x 軸の向き | 近似で実装 | 画面の線幅を測る |
| CSP 全体の筆圧カーブ | 入力 0.5 以上で 1.0 になった | 筆圧検知レベルの調節の設定と比べる |
| 92 バイトヘッダの未知語 | この環境では生成されない | CSP 2.x 以降で描いたファイル |
| Offscreen Parameter の 16 個の整数、InitColor | 全種類で同じ値 | 表現色(モノクロ・グレー)を変えたレイヤー |
| BlockStatus の意味 | 常に 1 | [外部] でも未解明 |
| ベクター消しゴム(交点まで消去)、塗り・フキダシの参照先 | 未調査 | `VectorNormalFillIndex` などを使う文書 |
| アニメーションのセル、テキスト、定規、3D | 範囲外 | clipfile-rs の文書を参照 |
