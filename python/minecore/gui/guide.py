"""Step-by-step user guide shown from the Help menu (Turkish, Markdown)."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QTextBrowser, QVBoxLayout, QWidget

GUIDE = """
# MineCore: adım adım kullanım

## 1. Proje ve sondaj verisi
1. **Proje > Yeni proje**: önce kaydedilecek klasörü (ör. Masaüstü), sonra proje adını
   sorar ve orada `<ad>.mcproj` klasörünü oluşturur. **Proje aç** için bu klasörü ya da
   onu içeren klasörü seçmeniz yeterli. Tüm veriler burada saklanır,
   **Geri al / Yinele** her işlemde çalışır.
2. **Sondaj > Klasörden içe aktar**: kuyu ağzı (collar), sapma (survey) ve analiz/litoloji
   dosyaları (CSV, TXT, XLSX). Sütunlar ve ayraçlar otomatik tanınır.
3. **Sondaj > Doğrula** (F5): çakışan aralıklar, eksik kuyular, sapma hataları
   *Doğrulama Raporu* sekmesinde listelenir.
4. **Sondaj > Kompozit oluştur**: sabit uzunlukta kompozitler (örn. 2 m).

## 2. Jeoistatistik
1. **Blok Modeli > Örnek istatistikleri**: histogram, log-olasılık grafiği, üst kesme
   (top-cut) etkisi ve kümelenme giderme.
2. **Blok Modeli > Variogram analizi**: her yönde veya yönlü, kuyu içi variogram;
   otomatik uydurma, iki yapılı model; modeli projeye kaydedin.
3. **Yüzey > Katı model içe aktar**: OBJ/STL/DXF cevher zonu (domen) katıları.
   Kesit yorumlarınız DXF çizgiyse **Çizgileri içe aktar** ve **Kesit çizgilerini bağla**.
4. **Yüzey > Örtük (implicit) domen modeli**: sondaj aralıklarından (tenör eşiği veya
   litoloji kodu) RBF ile kapalı domen katısı; yassı kütleler için anizotropi.
5. **Yüzey > Blok modelini katı ile işaretle**: alt hücre örneklemesiyle `_pct` kısmi
   blok oranı da yazılır; tenör-tonaj raporunda *Kısmi blok oranı* olarak seçin.

## 3. Blok modeli ve kestirim
1. **Blok Modeli > Blok modeli oluştur**: kompozitleri kapsayan düzenli model. Açık ocak
   için kenar payını geniş tutun (ocak modelin kenarına değmesin).
   Başka yazılımdan gelen model için **Blok modeli içe aktar (CSV)**.
2. **Yüzey > Topoğrafya**: noktalardan veya kuyu ağızlarından yüzey; **kaya/hava** ile
   bloklar işaretlenir.
3. **Blok Modeli > Tenör kestirimi**: NN, IDW veya sıradan kriging; isteğe bağlı domen
   katısı, yalnız kaya blokları ve üst kesme. Domenleri sırayla aynı özniteliğe
   kestirebilirsiniz.
4. **Kestirim doğrulama**: swath grafikleri ve kuyu dışarıda bırakmalı çapraz doğrulama.
5. **Güven kategorileri**: mesafe, örnek sayısı ve kriging varyansına göre kategori
   (JORC/NI 43-101 sınıflandırması değildir; yetkin kişi gerekir).
6. **Tenör-tonaj raporu**: tablo, eğri, Excel; domen/kategori/ocak filtresi.
7. **Hesaplayıcı**: ifadeyle yeni öznitelik, örn. `where(lito == 2, 2.65, 2.5)` veya
   çok elementli NSR.

## 4. Açık ocak
1. **Açık Ocak > Ocak optimizasyonu**: iç içe kabuklar (Lerchs-Grossmann ile aynı sonuç),
   en iyi/en kötü NPV, pushback'ler (minimum kazı genişliği), kısıtlı üretim çizelgesi,
   pushback-basamak envanteri, Excel raporu. Kazanım, seyrelme, NSR modu ve ek işleme
   yolları (ör. `liç:60:4`; her blok en yüksek marjlı yola gider) desteklenir.
2. **Basamak tasarımı**: şev açısı, basamak yüksekliği ve berm ile tasarım yüzeyi,
   tasarım içindeki bloklar ve değer karşılaştırması; isteğe bağlı spiral rampa
   (genişlik, eğim, çıkış yönü); geoteknik sektörler (ör. `0:70:6; 180:55:8`:
   kuzey şevinden başlayarak azimut:basamak açısı:berm); DXF.
3. **Cut-off optimizasyonu (Lane)**: maden, tesis ve pazar kapasitesine göre yıllara
   göre en uygun sınır tenör ve NPV.
4. **Ocağı DXF olarak dışa aktar**: basamak üst/alt çizgileri, ocak katısı, kazı sonrası yüzey.
5. **Dönem sonu yüzeyleri**: çizelgedeki her yılın sonundaki kazı yüzeyi ve DXF çizgileri.

## 5. Yeraltı
- **Yeraltı > Pano zarfı**: sabit boyutlu pano ile yüzen pano zarfı ve çakışmasız pano
  seçimi; DXF.
- **Yeraltı > Dekline ve kat galerileri**: ağızdan spiral dekline (eğim, dönüş yarıçapı,
  cevherden uzaklık), her kata yatay galeri ve cevher doğrultusunda kat galerisi
  (ore drive); metraj, hacim, tonaj; DXF.

## 6. Görünüm
- **3B Görünüm** sekmesinde renklendirme, blok sınır tenörü ve **kesit** (K-G, D-B, plan)
  kalınlığı ile ◀ ▶ adımları.
- **Kesiti yazdır (PDF/PNG)**: seçili kesit A3 olarak; blok kesiti, sondajlar,
  yüzeyler ve katılar.

## Komut satırı
Her işlem `minecore` komutuyla da çalışır, örn.
`minecore pit optimise proje.mcproj BM au_ok --price 2000 --price-unit ozt ...`.
Yardım için: `minecore --help`.
"""


class GuideDialog(QDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("MineCore kullanım kılavuzu")
        self.resize(820, 760)
        layout = QVBoxLayout(self)
        self.text = QTextBrowser()
        self.text.setMarkdown(GUIDE)
        layout.addWidget(self.text)
