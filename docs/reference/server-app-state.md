# Server app state detail payload

Current AtHome detail pages embed an SSR state object in:

```html
<script id="serverApp-state" type="application/json">
```

The payload is a preferred source for detail hydration because it is structured and
contains more complete unit, building, pickup, contract, and image data than the
rendered DOM. It is not a public versioned API. The parser must validate the expected
shape and fall back to the DOM parser when the script, wrapper, or required fields are
absent.

## Source selection

1. Parse `script#serverApp-state` as JSON.
2. Read the `first-view-ITEMS.propertyData` object.
3. Require `rentInfo` with a matching property key, price, and building name.
4. Hydrate structured fields from this payload.
5. Fall back to the current DOM selectors when the state payload is unavailable or
   fails validation.

Do not use dynamic request-keyed `G.text.*` entries as the primary path. They contain
transport metadata and unstable request keys. `first-view-ITEMS.propertyData` is the
stable SSR view-model path observed in the live capture.

## Verified schema

| JSON path | Type | Detail meaning |
|---|---|---|
| `first-view-ITEMS.propertyData.rentInfo.buildingNm` | string | Unit/building display name. |
| `...rentInfo.price` | string | Rent in 万円, for example `"7.8"`. |
| `...rentInfo.managementFee` | string | Management fee, for example `"7,200円"`. |
| `...rentInfo.deposit` | string | Raw deposit term, for example `"1ヶ月"`. |
| `...rentInfo.keyMoney` | string | Raw key-money term, for example `"なし"`. |
| `...rentInfo.kaidateKai` | string | Building floors and unit floor. |
| `...rentInfo.address` | string | Presented property address. |
| `...rentInfo.lineNm` / `stationNm` | string | Nearest transit line and station. |
| `...rentInfo.pickup` | object | Boolean PICK UP feature availability. |
| `...rentInfo.buildingInfo` | object | Building metadata and remarks. |
| `...rentInfo.buildingInfo.chikunengetsu` | string | Raw construction date. |
| `...rentInfo.buildingInfo.tatemonoKozo` | string | Building structure. |
| `...rentInfo.buildingInfo.sokosu` | string | Total unit count when supplied. |
| `...rentInfo.buildingInfo.madori` | string | Unit floor plan. |
| `...rentInfo.otherPropertyInfo.contract` | string | Contract period. |
| `...rentInfo.imageList` | array | Ordered image metadata; `subCategory="layout"` identifies a floor plan. |
| `...facilityFeatureList` | array | Feature category objects with `title` and comma-separated `text`. |

## Representative payload

This is a minimized, sanitized shape from a live Osaka rental detail capture. Image
paths and public listing fields are illustrative; endpoint keys, headers, operator
session data, and unrelated state are omitted.

```json
{
  "first-view-ITEMS": {
    "propertyData": {
      "rentInfo": {
        "buildingNm": "Example building 2階 1K",
        "prefectureNm": "大阪府",
        "prefectureRoman": "osaka",
        "cityNm": "大阪市中央区",
        "cityRoman": "osaka_chuo",
        "townNm": "谷町",
        "lineNm": "地下鉄長堀鶴見緑地線",
        "stationNm": "谷町六丁目",
        "price": "7.8",
        "deposit": "1ヶ月",
        "keyMoney": "なし",
        "managementFee": "7,200円",
        "kaidateKai": "15階建 / 2階",
        "address": "大阪府大阪市中央区谷町５丁目",
        "pickup": {
          "isSeparateBath": true,
          "hasBathDryer": true,
          "hasAutoLock": true,
          "hasMonitorIntercom": true,
          "hasDeliveryBox": true,
          "isFreeInternet": true,
          "isAbove2nd": true,
          "isNewBuild": true,
          "hasPetSodan": false,
          "hasParking": false
        },
        "buildingInfo": {
          "buildingNm": "Example building",
          "chikunengetsu": "2026年8月",
          "tatemonoKozo": "ＲＣ",
          "sokosu": "－",
          "madori": "１K（洋室６．６帖）",
          "biko": "賃貸保証等：加入要"
        },
        "otherPropertyInfo": {
          "contract": "2年",
          "bukkenNo": "example-id"
        },
        "imageList": [
          {
            "imageUrl": "/image_files/path/example",
            "title": "間取図",
            "subCategory": "layout"
          }
        ]
      },
      "facilityFeatureList": [
        {
          "title": "設備・サービス",
          "text": "宅配ＢＯＸ、室内洗濯機置場"
        }
      ]
    }
  }
}
```

## Validation and fallback

The structured payload is accepted only when:

- The script is parseable JSON.
- `first-view-ITEMS.propertyData.rentInfo` is an object.
- A stable listing identifier matches the requested detail URL or the document title.
- At least a property name, a non-empty raw price, and address or station data are
  present.

On failure, emit a debug-safe source marker and parse the current DOM instead. A
challenge page must still be detected before either source is trusted or persisted.
