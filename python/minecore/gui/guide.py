"""Step-by-step user guide shown from the Help menu (Turkish, Markdown)."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QTextBrowser, QVBoxLayout, QWidget

from minecore.gui.layout import fit_to_screen

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
   için kenar payını geniş tutun (ocak modelin kenarına değmesin). Cevher kuzey-güney
   ya da doğu-batı uzanmıyorsa **Döndürme açısı** ile modeli cevher doğrultusuna hizalayın
   (satır ekseninin kuzeyden saat yönünde açısı; örn. K35D doğrultu için 35). Döndürülmüş
   model kestirim, rapor, ocak optimizasyonu, tasarım, yeraltı ve kesitlerde aynen çalışır.
   Başka yazılımdan gelen model için **Blok modeli içe aktar (CSV)**; döndürülmüş bir
   modelse içe aktarırken açısını girin.
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
   pushback-basamak envanteri, Excel raporu. Kazanım, seyrelme, ödenebilir metal,
   royalty (devlet hakkı), NSR modu, yan ürünler (ör. `ag:25:ozt:70:95`; blok geliri
   tüm metallerin toplamıdır) ve ek işleme yolları (ör. `liç:60:4`; her blok en
   yüksek marjlı yola gider) desteklenir. Çizelgede stok tenör sınıfları (ör. `1; 2`),
   stok kapasitesi ve geri yükleme maliyeti verilebilir: tesis önce yüksek tenörlü
   cevherle beslenir, düşük tenör stokta bekler, dolu stoktan taşan cevher döküme gider.
   "Kazı/İşleme maliyet çarpanı" bir blok özniteliğiyle (ör. sert kayada 1.2) maliyeti
   blok blok çarpar (MCAF/PCAF). "Konsantre tenörü" girilirse ana yoldaki metal konsantre
   tonuna çevrilir; TC konsantre tonu başına düşülür, "Cezalı öğeler" (ör.
   `as:30:2000:1000:3`) serbest sınırı aşan her adım için ceza keser.
   "Pushback'leri NPV'ye göre seç" işaretlenirse kabuklar, çizelge NPV'sini en büyütecek
   şekilde gruplanır (her pushback en az cevher hedefini taşır; az kabukta tüm
   gruplamalar denenir, çokta yerel arama yapılır).
   Çizelge yöntemi olarak "NPV optimizasyonu (MILP)" seçilirse aynı kurallarla NPV'yi en
   büyüten çizelge bulunur (kurallı çizelgeden kötü olamaz; süre sınırı verilir).
   "Dönem uzunlukları" ile ilk dönemler kısa tutulabilir (ör. `0.25*4; 1`: dört çeyrek,
   sonra yıllar); "Dönemlere göre kazı/tesis" ile kapasite dönem dönem değişir (ör. tesis
   devreye alma `1*1; 1.5*1; 2`). Kapasiteler yıllıktır. "Harmanlama hedefleri" (ör.
   `tenör:0.8:1.5; as:-:500`) her dönem tesis beslemesinin ortalamasını sınırlar; yalnız
   MILP yöntemi uygular, çizelge tablosunda her hedefin dönem ortalaması gösterilir.
   **Proje nakit akışı** bölümünde yatırım (ör. `0:250; 4:40`, yıl:M$), sabit gider,
   kapanış maliyeti, vergi ve amortisman girilir; "Nakit akışı" sekmesi yıl yıl FAVÖK,
   vergi, nakit akışı ve proje NPV, İVO (IRR) ve geri ödeme süresini verir.
   "Ekipman" sekmesi çizelgenin her yılı için ortalama nakliye çevrimini (ocak içi,
   rampa ve yüzey mesafesi, dolu/boş hızlar), gereken kamyon ve yükleyici sayısını ve
   nakliye-yükleme maliyetini ($/t) verir.
   "Senaryolar" sekmesi çalışmayı adıyla kaydeder (tüm girdiler ve ana sonuçlar
   projede saklanır), senaryoları yan yana tablo ve NPV grafiği ile karşılaştırır,
   bir senaryonun girdilerini forma geri yükler ve karşılaştırmayı CSV'ye yazar.
   "Duyarlılık" sekmesi fiyat, madencilik ve zenginleştirme maliyeti, kazanım, iskonto
   ve yatırımın ±% değişiminde proje NPV'sini tablo ve örümcek grafiği olarak verir
   (sabit ocak tasarımı veya her durumda yeniden optimize edilen ocak).
2. **Basamak tasarımı**: şev açısı, basamak yüksekliği ve berm ile tasarım yüzeyi,
   tasarım içindeki bloklar ve değer karşılaştırması; isteğe bağlı spiral rampa
   (genişlik, eğim, çıkış yönü); geoteknik sektörler (ör. `0:70:6; 180:55:8`:
   kuzey şevinden başlayarak azimut:basamak açısı:berm); DXF. Tasarım birden çok ayrı
   ocak açarsa her ocağa ayrı rampa çizilir (diğer ocakların çıkış yönleri ayrıca
   verilebilir); birleşik ocaktaki ocak tabanları sayılır.
3. **Cut-off optimizasyonu (Lane)**: maden, tesis ve pazar kapasitesine göre yıllara
   göre en uygun sınır tenör ve NPV.
4. **Ocağı DXF olarak dışa aktar**: basamak üst/alt çizgileri, ocak katısı, kazı sonrası yüzey.
5. **Dönem sonu yüzeyleri**: çizelgedeki her yılın sonundaki kazı yüzeyi ve DXF çizgileri.

Delme-patlatma: **Açık Ocak > Delme-patlatma tasarımı** delik çapından dilim, aralık,
taban delgisi ve sıkılamayı, delik başına şarjı, özgül şarjı (kg/m³, kg/t), maliyeti ve
yıllık delik/patlayıcı ihtiyacını verir; kapalı bir sınır çizgisi içinde şaşırtmalı
delik planı oluşturup DXF'e yazar.

Döküm sahası: **Açık Ocak > Döküm sahası tasarımı** kapalı bir sınır içinde kat
yüksekliği, şev açısı ve bermle döküm yüzeyini oluşturur (zemin yüzeyi seçilebilir);
gevşek hacim, kabarma ve yoğunlukla yerinde ton kapasitesi, kat kat hacim ve
gereken pasa için yeterlilik verir.

## 5. Yeraltı
- **Yeraltı > Pano zarfı**: sabit boyutlu pano ile yüzen pano zarfı ve çakışmasız pano
  seçimi; DXF.
- **Pano ekonomisi**: aynı pencerede fiyat, kazanım, yeraltı madencilik ve
  zenginleştirme maliyeti, seyrelme ve kazı kazanımıyla her panonun değeri; pozitif
  değerli panolar kapasiteye göre seviye seviye (alttan veya üstten) yıllara dağıtılır;
  yatırım, sabit gider ve vergiyle proje nakit akışı, NPV ve İVO verilir.
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
        fit_to_screen(self, 820, 760)
        layout = QVBoxLayout(self)
        self.text = QTextBrowser()
        self.text.setMarkdown(GUIDE)
        layout.addWidget(self.text)
