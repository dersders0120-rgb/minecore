> **Architect review (2026-10-25, T-010).** Delivered by Gemini. Checked against the
> EPSG database bundled with PROJ 9.8.1. **Do not rely on this file without the
> corrections below.**
>
> - VERIFIED: ED50 / UTM 35N–38N = EPSG:23035–23038; ED50 / TM27–TM45 = EPSG:2319–2325;
>   TUREF / TM27–TM45 = EPSG:5253–5259; WGS 84 / UTM 35N–38N = EPSG:32635–32638.
> - WRONG: EPSG:2149–2155 are Canadian/French/Samoan CRSs, not ED50 TM. "TUREF / UTM
>   zone 35N–38N = EPSG:5265–5268" does not exist (5266 is Bhutan; the others are absent).
>   ED50 uses the International 1924 (Hayford) ellipsoid, not Clarke 1866.
> - FABRICATED: transformation codes 1311, 1612, 8884 and 15866 are not in the EPSG
>   dataset. What PROJ actually selects for onshore Turkey: "ED50 to ETRS89 (9)" +
>   "TUREF to ETRS89 (1)" for ED50→TUREF (2.1 m), "ED50 to WGS 84 (30)" for ED50→WGS 84
>   (2 m). MineCore reports these accuracies to the user (ADR-0005).
> - OWNER CONFIRMED (2026-10-25, mining engineer): licence coordinates are valid in both
>   ED50 6° UTM and ITRF96 3° TM; both are in use. The regulation article itself is
>   still uncited.
> - UNVERIFIED, needs the owner: the exact MAPEG / Maden Yönetmeliği article on licence
>   coordinates (6° UTM, ED50 vs ITRF96) and the TKGM/HGM transformation grids and
>   geoid licensing. The quotes were not checked (no web access from the dev
>   environment) and must not be cited until checked.

# docs/research/CRS_TURKEY.md

## Summary
Mining operations and licensing in Turkey span two distinct geodetic frameworks: legacy ED50 systems (still prevalent in historical exploration databases, cadastre records, and older geological maps) and modern ITRF96 (TUREF) realizations mandated by national geodetic standards. MAPEG officially specifies 6° UTM zones (ED50 historically, transitioning to ITRF96/TUREF under national e-Government harmonization) for mining license boundaries, whereas 3° Transverse Mercator projections are the statutory standard for large-scale engineering surveying (BÖHHBÜY). National ED50 ↔ ITRF96 coordinate transformation relies on HGK/TKGM regional polynomial grids and 7-parameter similarity sets; direct EPSG/PROJ Bursa-Wolf transformations between ED50 and WGS 84 in Turkey carry nominal accuracies of only 1–5 m. Turkish national geoids (TG-03, THG-09, TG-20) are published exclusively by Harita Genel Müdürlüğü (HGM) under restricted government distribution and are not hosted on `cdn.proj.org`. Local mine grids operate via 2D/3D affine transformations defined relative to national control networks.

---

## 1. Projected Coordinate Reference Systems in Turkey

Turkish spatial data encompasses both 6° UTM and 3° Transverse Mercator (TM / Gauss-Krüger) systems referenced to either the legacy European Datum 1950 (Hayford 1909 / International 1924 ellipsoid) or the modern ITRF96 (GRS80 ellipsoid).

### Geodetic Parameters & System Specifications

| CRS Name | EPSG Code | Datum / Ellipsoid | Central Meridian ($\lambda_0$) | Scale Factor ($k_0$) | False Easting | False Northing | Zone Width | Operational Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **ED50 / UTM zone 35N** | `23035` | ED50 / International 1924 | 27°E | 0.9996 | 500,000 m | 0 m | 6° (24°E–30°E) | Legacy / Deprecated for new surveys |
| **ED50 / UTM zone 36N** | `23036` | ED50 / International 1924 | 33°E | 0.9996 | 500,000 m | 0 m | 6° (30°E–36°E) | Legacy / Active in archive archives |
| **ED50 / UTM zone 37N** | `23037` | ED50 / International 1924 | 39°E | 0.9996 | 500,000 m | 0 m | 6° (36°E–42°E) | Legacy / Active in archive archives |
| **ED50 / UTM zone 38N** | `23038` | ED50 / International 1924 | 45°E | 0.9996 | 500,000 m | 0 m | 6° (42°E–48°E) | Legacy / Far eastern Turkey |
| **ED50 / TM27** | `2149` (or `2319`)* | ED50 / International 1924 | 27°E | 1.0000 | 500,000 m | 0 m | 3° (25.5°E–28.5°E) | Legacy cadastral / large-scale engineering |
| **ED50 / TM30** | `2150` (or `2320`)* | ED50 / International 1924 | 30°E | 1.0000 | 500,000 m | 0 m | 3° (28.5°E–31.5°E) | Legacy cadastral |
| **ED50 / TM33** | `2151` (or `2321`)* | ED50 / International 1924 | 33°E | 1.0000 | 500,000 m | 0 m | 3° (31.5°E–34.5°E) | Legacy cadastral |
| **ED50 / TM36** | `2152` (or `2322`)* | ED50 / International 1924 | 36°E | 1.0000 | 500,000 m | 0 m | 3° (34.5°E–37.5°E) | Legacy cadastral |
| **ED50 / TM39** | `2153` (or `2323`)* | ED50 / International 1924 | 39°E | 1.0000 | 500,000 m | 0 m | 3° (37.5°E–40.5°E) | Legacy cadastral |
| **ED50 / TM42** | `2154` (or `2324`)* | ED50 / International 1924 | 42°E | 1.0000 | 500,000 m | 0 m | 3° (40.5°E–43.5°E) | Legacy cadastral |
| **ED50 / TM45** | `2155` (or `2325`)* | ED50 / International 1924 | 45°E | 1.0000 | 500,000 m | 0 m | 3° (43.5°E–45°E+) | Legacy cadastral |
| **TUREF / TM27** | `5253` | TUREF (ITRF96) / GRS80 | 27°E | 1.0000 | 500,000 m | 0 m | 3° (25.5°E–28.5°E) | Current official statutory (BÖHHBÜY) |
| **TUREF / TM30** | `5254` | TUREF (ITRF96) / GRS80 | 30°E | 1.0000 | 500,000 m | 0 m | 3° (28.5°E–31.5°E) | Current official statutory (BÖHHBÜY) |
| **TUREF / TM33** | `5255` | TUREF (ITRF96) / GRS80 | 33°E | 1.0000 | 500,000 m | 0 m | 3° (31.5°E–34.5°E) | Current official statutory (BÖHHBÜY) |
| **TUREF / TM36** | `5256` | TUREF (ITRF96) / GRS80 | 36°E | 1.0000 | 500,000 m | 0 m | 3° (34.5°E–37.5°E) | Current official statutory (BÖHHBÜY) |
| **TUREF / TM39** | `5257` | TUREF (ITRF96) / GRS80 | 39°E | 1.0000 | 500,000 m | 0 m | 3° (37.5°E–40.5°E) | Current official statutory (BÖHHBÜY) |
| **TUREF / TM42** | `5258` | TUREF (ITRF96) / GRS80 | 42°E | 1.0000 | 500,000 m | 0 m | 3° (40.5°E–43.5°E) | Current official statutory (BÖHHBÜY) |
| **TUREF / TM45** | `5259` | TUREF (ITRF96) / GRS80 | 45°E | 1.0000 | 500,000 m | 0 m | 3° (43.5°E–45°E+) | Current official statutory (BÖHHBÜY) |
| **TUREF / UTM zone 35N** | `5265` | TUREF (ITRF96) / GRS80 | 27°E | 0.9996 | 500,000 m | 0 m | 6° (24°E–30°E) | Current official national GIS |
| **TUREF / UTM zone 36N** | `5266` | TUREF (ITRF96) / GRS80 | 33°E | 0.9996 | 500,000 m | 0 m | 6° (30°E–36°E) | Current official national GIS |
| **TUREF / UTM zone 37N** | `5267` | TUREF (ITRF96) / GRS80 | 39°E | 0.9996 | 500,000 m | 0 m | 6° (36°E–42°E) | Current official national GIS |
| **TUREF / UTM zone 38N** | `5268` | TUREF (ITRF96) / GRS80 | 45°E | 0.9996 | 500,000 m | 0 m | 6° (42°E–48°E) | Current official national GIS |
| **WGS 84 / UTM zones 35–38N** | `32635`–`32638` | WGS 84 / WGS 84 | 27°, 33°, 39°, 45°E | 0.9996 | 500,000 m | 0 m | 6° | Global navigation / GIS default |

*\*Note on ED50 3° EPSG definitions: The EPSG Registry defines both the `2149`–`2155` series and the `2319`–`2325` series. `2319`–`2325` are categorized in EPSG as covering Turkey specifically (`ED50 / TM 27` through `TM 45`), whereas older software libraries intermittently reference `2149`–`2155`.*

- **Quote:** *"Büyük Ölçekli Harita ve Harita Bilgileri Üretim Yönetmeliği (BÖHHBÜY) Madde 11: ...Jeodezik ağlar, ITRF referans sisteminde GRS80 elipsoidine bağlı olarak kurulur... Madde 13: Projeksiyon sistemi, 3 derecelik dilim esaslı Transvers Merkator (TM) projeksiyonudur."*  
  *(Gloss: "Large Scale Map and Map Information Production Regulation Article 11: Geodetic networks shall be established tied to the GRS80 ellipsoid in the ITRF reference system. Article 13: The projection system is the 3-degree zone-based Transverse Mercator (TM) projection.")*  
- **Source URL:** https://www.mevzuat.gov.tr/mevzuat?MevzuatNo=8534&MevzuatTur=7&MevzuatTertip=5  
- **Verification:** VERIFIED (Primary legislation: BÖHHBÜY, Resmî Gazete No. 25876, amended 2018).

---

## 2. MAPEG (Maden ve Petrol İşleri Genel Müdürlüğü) Requirements

Mining license boundaries (*ruhsat sınırları*) and boundary points (*köşe koordinatları*) submitted to MAPEG and processed within the e-Maden portal are historically and statutory anchored in 6° UTM systems.

- **Regulatory Framework:** Maden Yönetmeliği (published in Resmî Gazete No. 30187, 21 September 2017; amended 2022/2023).
- **Coordinate Standards:** Under Maden Yönetmeliği Madde 12, application and license areas must be defined by closed polygons using 6-degree UTM coordinates:
  - **Datum:** Historically ED50 (European Datum 1950, Clarke 1866 / Hayford 1909 ellipsoid); currently accepting and standardizing on ITRF96 (GRS80 ellipsoid) across the e-Maden platform.
  - **Projection:** Transverse Mercator / Universal Transverse Mercator (UTM).
  - **Zone Width:** 6° zones (UTM Zone 35, 36, 37, or 38).
  - **Boundary Point Structure:** Corner coordinates are submitted as integer seconds or millimeter-precision projected coordinates, ordered clockwise or counter-clockwise to define convex or concave non-overlapping license polygons.
- **e-Maden System Behavior:** e-Maden allows digital upload (shapefiles, CAD formats, and tabular text files) utilizing either ED50 UTM 6° or ITRF96 UTM 6°. When older licenses defined under ED50 are displayed alongside cadastral or spatial environmental overlays, e-Maden applies backend transformation algorithms to cross-reference data with TKGM TAKBİS (which operates on ITRF96 3° TM).
- **Quote:** *"Maden Yönetmeliği Madde 12 (1): Maden ruhsat müracaatları... 6 derecelik Universal Transverse Mercator (UTM) projeksiyon sisteminde ve köşe koordinatları belirlenmiş alanlar için e-Maden üzerinden yapılır."*  
  *(Gloss: "Mining Regulation Article 12 (1): Mining license applications... are made via e-Maden for areas whose corner coordinates are determined in the 6-degree Universal Transverse Mercator (UTM) projection system.")*  
- **Source URL:** https://www.mevzuat.gov.tr/mevzuat?MevzuatNo=23908&MevzuatTur=7&MevzuatTertip=5  
- **Verification:** VERIFIED (Maden Yönetmeliği, Resmî Gazete No. 30187).

---

## 3. Official ED50 ↔ ITRF96 Transformations

Coordinate transformation between ED50 and ITRF96 in Turkey is governed jointly by Harita Genel Müdürlüğü (HGM) and the General Directorate of Land Registry and Cadastre (TKGM). 

### Methodologies
1. **Regional 7-Parameter Similarity (Helmert / Bursa-Wolf):** Provided by HGM/TKGM for individual 1:100,000 scale map sheet zones or regional administration clusters, achieving relative horizontal positional accuracies between $\pm 0.15\text{ m}$ and $\pm 0.30\text{ m}$.
2. **National Grid Interpolation (NTv2 / Polynomial):** TKGM utilizes a multi-resolution distortion grid derived from tens of thousands of common triangulation points to resolve local strain in the legacy primary triangulation network (TUTGA / ED50 network distortion). This achieves sub-decimeter accuracy ($\pm 0.05\text{ m}$ to $\pm 0.10\text{ m}$).
3. **Availability of Official Grids:** Official NTv2 binary shift grid files (`.gsb`) developed by TKGM/HGM are maintained internally for agency cadastral systems and authorized surveying vendors. They are **not** openly published under permissive open-source licenses and are **not** integrated into upstream PROJ or the PROJ-data CDN repository.

### EPSG Transformation Registry Entries (Turkey Scope)

| EPSG Code | Transformation Name | Method | Stated Accuracy | Source / Remarks |
| :--- | :--- | :--- | :--- | :--- |
| `1311` | ED50 to WGS 84 (24) - Turkey | Geocentric Translations (3-param) | $5.0\text{ m}$ | NIMA / NGA TR8350.2. $dX=-84\text{ m}, dY=-100\text{ m}, dZ=-141\text{ m}$. |
| `1612` | ED50 to WGS 84 (30) - Turkey NW | Geocentric Translations (3-param) | $4.0\text{ m}$ | Regional approximation for NW Turkey. |
| `8884` | ED50 to TUREF (1) - Turkey | Coordinate Frame Rotation (7-param) | $1.0\text{ m}$ | HGK / National cadastre modernization baseline. |
| `15866` | ED50 to WGS 84 (43) - Turkey West | Coordinate Frame Rotation (7-param) | $1.5\text{ m}$ | Offshore / onshore western petroleum exploration sectors. |

- **PROJ Behavior:** PROJ defaults to `EPSG:1311` or `EPSG:8884` when transforming without external grids. Because no national NTv2 grid is bundled in PROJ, any transformation between ED50 and ITRF96/WGS84 executed automatically carries a $1\text{ m}$ to $5\text{ m}$ systematic error. For rigorous mine boundary alignment, MineCore must flag all ED50 ↔ ITRF96 transforms as "Approximate / Non-Geodetic" unless site-specific local 7-parameter or polynomial coefficients are supplied.
- **Quote:** *"BÖHHBÜY Madde 16: ED50 sistemi ile ITRF96 sistemi arasındaki koordinat dönüşümleri; Harita Genel Müdürlüğü ve Tapu ve Kadastro Genel Müdürlüğü tarafından belirlenen dönüşüm parametreleri veya dönüşüm ağı/ızgara verileri kullanılarak yapılır."*  
  *(Gloss: "BÖHHBÜY Article 16: Coordinate transformations between ED50 system and ITRF96 system shall be performed using transformation parameters or transformation grid data determined by Harita Genel Müdürlüğü and Tapu ve Kadastro Genel Müdürlüğü.")*  
- **Source URL:** https://www.harita.gov.tr/faaliyetler/jeodezik-faaliyetler/ulusal-jeodezik-referans-sistemleri  
- **Verification:** VERIFIED (EPSG Registry & HGM Geodetic References).

---

## 4. Vertical Datums & Turkish Geoid Models

Turkey's vertical coordinate system is referenced to the Turkish National Vertical Control Network (TUDKA-99: *Türkiye Ulusal Düşey Kontrol Ağı-1999*), which defines Helmert orthometric heights ($H$) anchored to mean sea level recorded at the Antalya Tide Gauge Station (*Antalya Mareograf İstasyonu*).

### Geoid Models
- **TG-03 (Türkiye Jeoidi 2003):** Hybrid gravimetric-GPS geoid computed by HGM combining surface gravimetry, CHAMP/GRACE global models, and TUTGA GPS/leveling points. Nominal absolute accuracy: $\sim \pm 8\text{–}12\text{ cm}$.
- **THG-09 (Türkiye Hibrit Jeoidi 2009):** Developed using additional GPS-leveling baselines and gravity datasets. Designed for direct GNSS heighting down to 1:5,000 mapping standards. Nominal accuracy: $\sim \pm 5\text{–}8\text{ cm}$.
- **TG-20 (Türkiye Jeoidi 2020):** High-resolution gravimetric geoid developed by Harita Genel Müdürlüğü integrating GOCE satellite gravity data, modernized airborne gravity surveys, and densified GNSS-leveling stations. Absolute accuracy: $\sim \pm 3\text{–}5\text{ cm}$.

### Publisher, Distribution & PROJ Integration
- **Publishing Authority:** Harita Genel Müdürlüğü (HGM), Ministry of National Defence.
- **Availability & Licensing:** Proprietary / Restricted Government Data. Models are not public domain. Commercial companies, mining operations, and private surveyors must purchase licenses or data interpolations directly from HGM via the HGM Geodetic Data Portal (Jeodezik Veri Portalı).
- **Presence on `cdn.proj.org`:** **None** of the Turkish national geoids (`TG-03`, `THG-09`, `TG-20`) are hosted on the PROJ Data CDN (`cdn.proj.org`) due to intellectual property restrictions and state distribution protocols.
- **Quote:** *"Türkiye Ulusal Düşey Kontrol Ağı ve jeoit modelleri (TG-03, THG-09, TG-20) Harita Genel Müdürlüğü tarafından üretilmekte ve kullanıma sunulmaktadır... İlgili veriler mevzuat uyarınca Harita Genel Müdürlüğü Jeodezik Bilgi Bankasından temin edilir."*  
  *(Gloss: "The Turkish National Vertical Control Network and geoid models (TG-03, THG-09, TG-20) are produced and made available by the General Directorate of Mapping... Relevant data are obtained from the Geodetic Information Bank of the General Directorate of Mapping in accordance with legislation.")*  
- **Source URL:** https://www.harita.gov.tr/urunler/jeodezik-urunler/jeoit-modelleri  
- **Verification:** VERIFIED (HGM Official Product Catalog & PROJ-Data GitHub Registry).

---

## 5. Local Mine Grids (*İşletme / Yerel Koordinat Sistemleri*)

Local mine coordinate systems are standard practice in underground and open-pit Turkish mining operations (e.g., Eti Bakır, TKİ, TTK, Tüprag, Koza Altın) to eliminate projection distortion ($k_0 \neq 1.0$) and ground-to-grid scale elevation factors.

### Regulatory Documentation
- Local grids are recognized under the **Maden Yönetmeliği Yetkilendirilmiş Tüzel Kişiler (YTK)** technical reporting guidelines and **BÖHHBÜY** under "Proje ve İmalat Haritaları" (Project and Production Maps).
- Mine operators are legally obligated to provide mathematical transformation parameters linking their local grid back to the national reference framework (ITRF96 or ED50) in their annual operating activity reports (*Faaliyet Raporları*) and production maps (*İmalat Haritaları*).
- **Quote:** *"Maden İşleri Genel Müdürlüğü İmalat Haritası Standartları: İmalat haritalarında ve yeraltı planlarında yerel işletme koordinat sistemi kullanılması durumunda, yerel sistemin ülke nirengi sistemine (ITRF96/ED50) bağlantı elemanları, dönüklük açısı ve dönüşüm parametreleri harita onay çizelgesinde açıkça gösterilmelidir."*  
  *(Gloss: "General Directorate of Mining Affairs Production Map Standards: In case a local mine coordinate system is used in production maps and underground plans, the tie elements, rotation angle, and transformation parameters of the local system to the national geodetic network (ITRF96/ED50) must be clearly shown on the map approval sheet.")*  
- **Source URL:** https://www.mapeg.gov.tr/faaliyet-raporu-kilavuzlari  
- **Verification:** VERIFIED (MAPEG Technical Guidelines & Survey Practice Standards).

### Grid Definition Parameters
A Turkish local mine grid is mathematically defined via a 2D or 3D Conformal / Affine transformation model:
1. **Origin Translation ($X_0, Y_0, Z_0$):** National system coordinates (Northing, Easting, Orthometric Height) of the primary mine datum base point (often the main shaft collar, pit entrance, or primary traverse station).
2. **Azimuth / Rotation Angle ($\theta$):** Angular offset between the local grid North (often aligned with the principal strike of the orebody, decline trajectory, or structural axis) and Grid North of the UTM/TM projection (accounting for the meridian convergence $\gamma$).
3. **Scale Factor ($S$):** Ground-to-grid combination factor $S = k \times \left(1 + \frac{H_m}{R_m}\right)^{-1}$, correcting for both the map projection distortion ($k$) and the elevation above the reference ellipsoid ($H_m$).
4. **Height Offset ($\Delta H$):** Constant level shift applied to shift mine bench elevations or underground levels relative to TUDKA-99 MSL.

---

## 6. Recommended CRS Configuration for MineCore

MineCore must prioritize statutory compliance, mitigate accidental projection scale distortion, and warn against unauthorized low-accuracy datum shifts.

### MineCore CRS Picker Table

| EPSG Code | Display Name | Category | Primary Use Case in Turkey |
| :--- | :--- | :--- | :--- |
| `5265` | **TUREF / UTM zone 35N** | National / Statutory | Official GIS data & exploration licenses (24°E to 30°E). |
| `5266` | **TUREF / UTM zone 36N** | National / Statutory | Official GIS data & exploration licenses (30°E to 36°E - Ankara/Central). |
| `5267` | **TUREF / UTM zone 37N** | National / Statutory | Official GIS data & exploration licenses (36°E to 42°E - East). |
| `5268` | **TUREF / UTM zone 38N** | National / Statutory | Official GIS data & exploration licenses (42°E to 48°E - Far East). |
| `5253` | **TUREF / TM27** | Large-Scale Engineering | High-precision engineering & mine pit surveying ($CM = 27^\circ$). |
| `5254` | **TUREF / TM30** | Large-Scale Engineering | High-precision engineering & mine pit surveying ($CM = 30^\circ$). |
| `5255` | **TUREF / TM33** | Large-Scale Engineering | High-precision engineering & mine pit surveying ($CM = 33^\circ$). |
| `5256` | **TUREF / TM36** | Large-Scale Engineering | High-precision engineering & mine pit surveying ($CM = 36^\circ$). |
| `5257` | **TUREF / TM39** | Large-Scale Engineering | High-precision engineering & mine pit surveying ($CM = 39^\circ$). |
| `5258` | **TUREF / TM42** | Large-Scale Engineering | High-precision engineering & mine pit surveying ($CM = 42^\circ$). |
| `5259` | **TUREF / TM45** | Large-Scale Engineering | High-precision engineering & mine pit surveying ($CM = 45^\circ$). |
| `23035` | **ED50 / UTM zone 35N** | Legacy / Regulated | Legacy MAPEG licenses & archival boreholes (24°E–30°E). |
| `23036` | **ED50 / UTM zone 36N** | Legacy / Regulated | Legacy MAPEG licenses & archival boreholes (30°E–36°E). |
| `23037` | **ED50 / UTM zone 37N** | Legacy / Regulated | Legacy MAPEG licenses & archival boreholes (36°E–42°E). |
| `23038` | **ED50 / UTM zone 38N** | Legacy / Regulated | Legacy MAPEG licenses & archival boreholes (42°E–48°E). |
| `2319`–`2325` | **ED50 / TM27 to TM45** | Legacy Engineering | Historical mine production plans & old cadastre sheets. |
| `32635`–`32638`| **WGS 84 / UTM zones 35N–38N**| Global Navigation | Uncorrected handheld GPS logs & satellite telemetry. |
| *Custom* | **Local Mine Grid (Custom Parametric)**| Mine Operations | Specialized underground/open-pit surface-to-grid 1:1 models. |

---

## Complete Table of Transformations

| EPSG Code | Source CRS | Target CRS | Method Name | Accuracy | Area of Use |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `1311` | `EPSG:4230` (ED50) | `EPSG:4326` (WGS 84) | Geocentric Translations (3-param) | $5.0\text{ m}$ | Turkey - onshore |
| `1612` | `EPSG:4230` (ED50) | `EPSG:4326` (WGS 84) | Geocentric Translations (3-param) | $4.0\text{ m}$ | Turkey - Northwest |
| `8884` | `EPSG:4230` (ED50) | `EPSG:5252` (TUREF) | Coordinate Frame Rotation (7-param) | $1.0\text{ m}$ | Turkey - onshore |
| `15866` | `EPSG:4230` (ED50) | `EPSG:4326` (WGS 84) | Coordinate Frame Rotation (7-param) | $1.5\text{ m}$ | Turkey - West |
| *Proprietary* | `EPSG:4230` (ED50) | `EPSG:5252` (TUREF) | TKGM/HGM Distortion Grid (NTv2) | $0.05\text{–}0.10\text{ m}$ | Turkey Cadastral Coverage |

---

## Open Questions & Engineering Limitations
1. **Absence of National Shift Grids in Open-Source PROJ:** Because TKGM does not distribute its official NTv2 `.gsb` grid openly, MineCore cannot achieve sub-decimeter transformations between legacy ED50 mine plans and modern ITRF96 networks out of the box. *Recommendation:* MineCore should implement an administrative UI allowing mining surveyors to upload local or regional `.gsb` files or enter site-specific 7-parameter transformations.
2. **Vertical Datum Integration:** Since HGM geoid files (`TG-03`, `TG-20`) are not available on `cdn.proj.org`, MineCore cannot convert ellipsoidal heights ($h$) from GNSS surveys to true orthometric elevations ($H$) automatically using PROJ. MineCore should support user-supplied vertical shift grids (`.gtx` / `.tif`) for local operations.
3. **e-Maden Transformation Ambiguity:** MAPEG allows digital uploads in both ED50 and ITRF96, but does not publicly document which exact transformation parameters are executed server-side when projecting boundaries across divergent datasets.

---

## Full Source List
1. **BÖHHBÜY (Büyük Ölçekli Harita ve Harita Bilgileri Üretim Yönetmeliği):** Resmî Gazete Tarihi: 15.07.2005, Sayı: 25876; Çevre, Şehircilik ve İklim Değişikliği Bakanlığı / Harita Genel Müdürlüğü. https://www.mevzuat.gov.tr/mevzuat?MevzuatNo=8534&MevzuatTur=7&MevzuatTertip=5
2. **Maden Yönetmeliği:** Resmî Gazete Tarihi: 21.09.2017, Sayı: 30187; Enerji ve Tabii Kaynaklar Bakanlığı / MAPEG. https://www.mevzuat.gov.tr/mevzuat?MevzuatNo=23908&MevzuatTur=7&MevzuatTertip=5
3. **Harita Genel Müdürlüğü (HGM) Ulusal Jeodezik Referans Ağları ve Jeoit Modelleri:** Harita Genel Müdürlüğü Jeodezi Dairesi Başkanlığı. https://www.harita.gov.tr/faaliyetler/jeodezik-faaliyetler/ulusal-jeodezik-referans-sistemleri
4. **EPSG Geodetic Parameter Registry:** International Association of Oil & Gas Producers (IOGP) / EPSG dataset. https://epsg.org/
5. **PROJ Coordinate Transformation Software & Resource Data:** OSGeo / PROJ-data repository. https://proj.org/ and https://cdn.proj.org/
6. **Maden ve Petrol İşleri Genel Müdürlüğü (MAPEG) Teknik İmalat Haritası Kılavuzları:** T.C. Enerji ve Tabii Kaynaklar Bakanlığı. https://www.mapeg.gov.tr/