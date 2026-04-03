export const trMessages: Record<string, string> = {
  "sidebar.appName": "Septum",
  "sidebar.tagline": "Yapay Zeka Gizlilik Geçidi",
  "sidebar.chat": "Sohbet",
  "sidebar.documents": "Dokümanlar",
  "sidebar.chunks": "Parçalar",
  "sidebar.settings": "Ayarlar",
  "sidebar.regulations": "Regülasyonlar",
  "sidebar.audit trail": "Denetim Kaydı",
  "sidebar.error logs": "Hata Günlükleri",
  "sidebar.footer": "Gizlilik-öncelikli · Yerel-öncelikli",

  "errorLogs.title": "Hata Günlükleri",
  "errorLogs.subtitle":
    "Uygulama tarafından kaydedilen backend ve frontend hatalarını görüntüleyin ve temizleyin.",
  "errorLogs.loading": "Hata günlükleri yükleniyor…",
  "errorLogs.empty": "Hata günlüğü yok.",
  "errorLogs.clearAll": "Tüm günlükleri temizle",
  "errorLogs.clearing": "Temizleniyor…",
  "errorLogs.confirm.clearAll":
    "Tüm hata günlüklerini temizlemek istediğinizden emin misiniz? Bu işlem geri alınamaz.",
  "errorLogs.filter.source": "Kaynak",
  "errorLogs.filter.allSources": "Tüm kaynaklar",
  "errorLogs.filter.level": "Seviye",
  "errorLogs.filter.allLevels": "Tüm seviyeler",
  "errorLogs.column.time": "Zaman",
  "errorLogs.column.source": "Kaynak",
  "errorLogs.column.level": "Seviye",
  "errorLogs.column.message": "Mesaj",
  "errorLogs.column.path": "Yol",
  "errorLogs.column.status": "Durum",
  "errorLogs.showDetail": "Detay",
  "errorLogs.hideDetail": "Gizle",
  "errorLogs.stackTrace": "Yığın izi",
  "errorLogs.extra": "Ek",
  "errorLogs.noDetail": "Yığın izi veya ek veri yok.",
  "errorLogs.paginationSummary": "Toplam {total} · sayfa {page} / {totalPages}",
  "errorLogs.prevPage": "Önceki",
  "errorLogs.nextPage": "Sonraki",
  "errorLogs.badgeAriaLabel": "{count} hata kayıtlı",

  "chat.title": "Sohbet",
  "chat.subtitle":
    "Septum'un gizlilik odaklı asistanıyla etkileşime geç. Doküman seç, sorular sor; yanıtlar anlık olarak akar.",
  "chat.uploading": "Doküman yükleniyor…",
  "chat.uploadSuccess": "Doküman başarıyla yüklendi.",
  "chat.uploadError": "Doküman yüklenirken bir hata oluştu. Lütfen tekrar deneyin.",
  "chat.loadingSettings": "Ayarlar yükleniyor…",

  "documents.title": "Dokümanlar",
  "documents.subtitle": "Yükle, incele ve işlenmiş dokümanları yönet.",
  "documents.uploading": "Doküman yükleniyor…",
  "documents.table.loading": "Doküman listesi yükleniyor…",
  "documents.table.empty": "Henüz doküman yüklenmedi.",
  "documents.table.column.document": "Doküman",
  "documents.table.column.type": "Tür",
  "documents.table.column.size": "Boyut",
  "documents.table.column.status": "Durum",
  "documents.table.column.chunks": "Parçalar",
  "documents.table.column.entities": "Varlıklar",
  "documents.table.column.actions": "İşlemler",
  "documents.table.languageLabel": "Dil",
  "documents.table.regulationsLabel": "Regülasyonlar",
  "documents.status.completed": "Tamamlandı",
  "documents.status.processing": "İşleniyor",
  "documents.status.pending": "Beklemede",
  "documents.status.failed": "Hata",
  "documents.actions.preview": "Önizleme",
  "documents.actions.transcription": "Transkripsiyon",
  "documents.actions.delete": "Sil",
  "documents.actions.reprocess": "Yeniden İşle",
  "documents.confirm.delete":
    '"{name}" dokümanını silmek istediğinizden emin misiniz?',
  "documents.confirm.reprocess":
    '"{name}" yeniden işlensin mi? Mevcut anonimleştirici ve regülasyonlarla anonimleştirme haritası ve indeksler yeniden oluşturulacaktır.',
  "documents.actions.deleteAll": "Tüm dokümanları sil",
  "documents.actions.deletingAll": "Tüm dokümanlar siliniyor…",
  "documents.confirm.deleteAll":
    "Tüm dokümanları silmek istediğinizden emin misiniz? Bu işlem geri alınamaz.",

  "chunks.title": "Parçalar",
  "chunks.subtitle":
    "Aşağıdan bir dokümanı genişleterek anonimize edilmiş parçalarını görüntüleyip düzenleyin.",
  "chunks.loadingDocuments": "Dokümanlar yükleniyor…",
  "chunks.emptyHint":
    "Henüz parçalara ayrılmış bir doküman yok. Önce Dokümanlar sayfasından bir dosya yükleyip içe aktarın.",
  "chunks.search.label": "Parçalar hakkında soru sor",
  "chunks.search.placeholder": "Sorunuzu buraya yazın…",
  "chunks.search.documentLabel": "Bu dokümanda ara",
  "chunks.search.documentPlaceholder": "Arama yapılacak dokümanı seçin",
  "chunks.search.button": "Parçalarda ara",
  "chunks.search.searching": "Aranıyor…",
  "chunks.search.resultsTitle": "Arama sonuçları ({count})",
  "chunks.search.clear": "Sonuçları temizle",
  "chunks.search.noResults": "Bu soruya uyan parça bulunamadı.",

  "settings.title": "Ayarlar",
  "settings.subtitle":
    "Bulut LLM'leri, gizlilik katmanlarını, yerel modelleri ve RAG davranışını yapılandırın.",
  "settings.loading": "Ayarlar yükleniyor...",
  "settings.tabs.cloud.label": "Bulut LLM",
  "settings.tabs.cloud.description": "Sağlayıcı ve model",
  "settings.tabs.privacy.label": "Gizlilik ve Anonimleştirme",
  "settings.tabs.privacy.description": "Onay ve maskeleme",
  "settings.tabs.local.label": "Yerel Modeller",
  "settings.tabs.local.description": "Ollama ve de-anon",
  "settings.tabs.rag.label": "RAG",
  "settings.tabs.rag.description": "Parçalama ve getirme",
  "settings.tabs.ingestion.label": "İçe Aktarım",
  "settings.tabs.ingestion.description": "Whisper ve OCR",
  "settings.tabs.textNormalization.label": "Metin normalizasyonu",
  "settings.tabs.textNormalization.description": "Regex tabanlı düzeltmeler",
  "settings.tabs.ner.label": "NER Modelleri",
  "settings.tabs.ner.description": "Dil → model eşlemesi",
  "settings.ner.sectionDescription":
    "Dil kodlarından HuggingFace NER modellerine varsayılan eşlemeyi görüntüleyin. Gelecekte üstüne yazma (override) kalıcı hale getirilecektir.",
  "settings.ner.table.language": "Dil",
  "settings.ner.table.model": "Model",
  "settings.ner.table.actions": "İşlemler",
  "settings.ner.overrideLabel": "{lang} için modeli değiştir",
  "settings.ner.restoreDefault": "Varsayılana döndür",
  "settings.ner.saveOverrides": "Değişiklikleri kaydet",
  "settings.common.saving": "Kaydediliyor…",

  "settings.common.testing": "Test ediliyor...",
  "settings.common.testConnection": "Bağlantıyı Test Et",

  "settings.cloud.sectionTitle": "Bulut LLM Ayarları",
  "settings.cloud.sectionDescription":
    "Birincil bulut LLM sağlayıcısını ve modelini yapılandırın. Bu ayarlar tüm uzak tamamlamalar için kullanılır.",
  "settings.cloud.provider.label": "LLM Sağlayıcı",
  "settings.cloud.provider.hint":
    "Backend yönlendiricisi tarafından kullanılan sağlayıcı tanımlayıcısı.",
  "settings.cloud.model.label": "LLM Model",
  "settings.cloud.model.hint":
    "Sağlayıcınızın beklediği tam model kimliği.",
  "settings.cloud.test.success":
    "Bulut LLM bağlantı testi başarılı.",
  "settings.cloud.test.failed":
    "Bulut LLM bağlantı testi başarısız.",

  "settings.privacy.sectionTitle": "Gizlilik ve Anonimleştirme",
  "settings.privacy.sectionDescription":
    "De-anonimleştirme davranışını, onay sürecini ve aktif anonimleştirme katmanlarını kontrol edin.",
  "settings.privacy.deanon.label": "De-anonimleştirme etkin",
  "settings.privacy.deanon.description":
    "Yanıtlar gösterilmeden önce yer tutucuların yerelde de-anonimleştirilmesine izin verin.",
  "settings.privacy.deanonStrategy.label":
    "De-anonimleştirme stratejisi",
  "settings.privacy.deanonStrategy.hint":
    "Strateji tanımlayıcısı (örneğin 'simple').",
  "settings.privacy.requireApproval.label":
    "Varsayılan olarak onay iste",
  "settings.privacy.requireApproval.description":
    "Maskelenmiş parçalar bulut LLM'lere gönderilmeden önce açık onay iste.",
  "settings.privacy.showJson.label": "JSON çıktısını göster",
  "settings.privacy.showJson.description":
    "Hata ayıklama için sohbet yanıtlarının yanında ham JSON yüklerini göster.",
  "settings.privacy.layers.title": "Anonimleştirme katmanları",
  "settings.privacy.layers.presidio.label": "Presidio katmanı",
  "settings.privacy.layers.presidio.description":
    "Kural tabanlı tanıyıcılar ve ulusal kimlik doğrulayıcıları.",
  "settings.privacy.layers.ner.label": "NER katmanı",
  "settings.privacy.layers.ner.description":
    "Dile özgü HuggingFace NER modelleri.",
  "settings.privacy.layers.ollamaValidation.label": "Ollama doğrulama",
  "settings.privacy.layers.ollamaValidation.description":
    "Yerel LLM bağlam ve regülasyon farkındalığı ile yanlış pozitifleri filtrele.",
  "settings.privacy.layers.ollama.label": "Ollama lakap katmanı",
  "settings.privacy.layers.ollama.description":
    "Yerel LLM kullanarak takma adları ve dolaylı referansları tespit et.",

  "settings.local.sectionTitle": "Yerel Model Ayarları",
  "settings.local.sectionDescription":
    "Sohbet ve de-anonimleştirme için kullanılacak yerel Ollama uç noktasını ve modelleri yapılandırın.",
  "settings.local.test.success":
    "Yerel model bağlantı testi başarılı.",
  "settings.local.test.failed":
    "Yerel model bağlantı testi başarısız.",
  "settings.local.baseUrl.label": "Ollama temel URL",
  "settings.local.baseUrl.hint":
    "Yerel Ollama örneğinizin temel URL'si.",
  "settings.local.chatModel.label": "Sohbet modeli",
  "settings.local.chatModel.hint":
    "Yerel sohbet için kullanılan Ollama model adı.",
  "settings.local.deanonModel.label": "De-anonimleştirme modeli",
  "settings.local.deanonModel.hint":
    "Yerel de-anonimleştirme için kullanılan Ollama model adı.",

  "settings.rag.sectionTitle": "RAG Ayarları",
  "settings.rag.sectionDescription":
    "Vektör deposu için parça boyutlarını ve getirme parametrelerini ayarlayın.",
  "settings.rag.defaultChunkSize.label": "Varsayılan parça boyutu",
  "settings.rag.defaultChunkSize.description":
    "Metin parçaları için yaklaşık karakter uzunluğu.",
  "settings.rag.chunkOverlap.label": "Parça örtüşmesi",
  "settings.rag.chunkOverlap.description":
    "Ardışık parçalar arasındaki örtüşen karakter sayısı.",
  "settings.rag.topK.label": "Top‑K getirme",
  "settings.rag.topK.description":
    "Sorgu başına getirilen varsayılan parça sayısı.",
  "settings.rag.formatSpecific.title": "Formata özel parça boyutları",
  "settings.rag.pdfChunkSize.label": "PDF parça boyutu",
  "settings.rag.pdfChunkSize.description":
    "PDF'ler için parça boyutu geçersiz kılması.",
  "settings.rag.audioChunkSize.label":
    "Ses parça boyutu (saniye)",
  "settings.rag.audioChunkSize.description":
    "Transkripsiyon parçaları için ses pencere uzunluğu.",
  "settings.rag.spreadsheetChunkSize.label":
    "Tablo parça boyutu",
  "settings.rag.spreadsheetChunkSize.description":
    "Her tablo parçası için maksimum hücre sayısı.",

  "settings.ingestion.sectionTitle": "İçe Aktarım Ayarları",
  "settings.ingestion.sectionDescription":
    "Whisper transkripsiyonu, OCR dilleri ve eklerin/gömülü varlıkların nasıl işlendiğini kontrol edin.",
  "settings.ingestion.audioHealth.title": "Ses hattı durumu",
  "settings.ingestion.audioHealth.description":
    "ffmpeg ve yapılandırılmış Whisper modelinin erişilebilir olup olmadığını kontrol eder.",
  "settings.ingestion.audioHealth.installButton":
    "Whisper modelini yükle",
  "settings.ingestion.audioHealth.installPending":
    "Yükleniyor…",
  "settings.ingestion.audioHealth.checking": "Kontrol ediliyor…",
  "settings.ingestion.audioHealth.unknown": "bilinmiyor",
  "settings.ingestion.audioHealth.ffmpegHint":
    "ffmpeg'i elle yükleyin (örneğin macOS için:",
  "settings.ingestion.health.readFailed":
    "İçe aktarma sağlık durumu okunamadı.",
  "settings.ingestion.health.installFailed":
    "Whisper modeli yüklenirken veya okunurken hata oluştu.",
  "settings.ingestion.audioHealth.whisperPackageLabel": "Whisper paketi:",
  "settings.ingestion.audioHealth.whisperModelLabel": "Whisper modeli:",
  "settings.ingestion.whisperModel.label": "Whisper modeli",
  "settings.ingestion.whisperModel.hint":
    "Ses transkripsiyonu için yerel Whisper model boyutu.",
  "settings.ingestion.defaultAudioLanguage.label": "Varsayılan ses dili",
  "settings.ingestion.defaultAudioLanguage.auto": "Otomatik algıla",
  "settings.ingestion.defaultAudioLanguage.hint":
    "Ayarlanırsa Whisper ses dilini (örn. Türkçe) bilir; bu da transkripsiyon doğruluğunu artırır. Karışık dilli dosyalar için Otomatik algıla bırakın.",
  "settings.ingestion.ocrLanguages.label":
    "OCR dilleri (virgülle ayrılmış)",
  "settings.ingestion.ocrLanguages.hint":
    "Seçilen OCR motoru için dil kodları (örn. en, tr, de).",
  "settings.ingestion.extractImages.label":
    "Gömülü görselleri çıkar",
  "settings.ingestion.extractImages.description":
    "Mümkün olduğunda dokümanlara gömülü görselleri çıkar ve işle.",
  "settings.ingestion.recursiveEmail.label":
    "Özyinelemeli e-posta ekleri",
  "settings.ingestion.recursiveEmail.description":
    "E-posta arşivlerinde bulunan ekleri özyinelemeli olarak içe aktar.",

  "settings.textNormalization.sectionTitle": "Metin normalizasyon kuralları",
  "settings.textNormalization.sectionDescription":
    "Anonimleştirmeden sonra uygulanan regex tabanlı metin normalizasyon kurallarını tanımlayın. Bunu sistematik OCR hatalarını düzeltmek veya ham içeriği bozmadan projeye özel temizlemeler uygulamak için kullanın.",
  "settings.textNormalization.newRuleTitle": "Yeni normalizasyon kuralı",
  "settings.textNormalization.fields.name": "Kural adı",
  "settings.textNormalization.fields.pattern": "Regex deseni",
  "settings.textNormalization.fields.replacement": "Yerine geçecek metin",
  "settings.textNormalization.fields.priority": "Öncelik",
  "settings.textNormalization.fields.isActive": "Kural aktif",
  "settings.textNormalization.actions.create": "Kural oluştur",
  "settings.textNormalization.actions.creating": "Oluşturuluyor…",
  "settings.textNormalization.table.name": "Ad",
  "settings.textNormalization.table.pattern": "Desen",
  "settings.textNormalization.table.replacement": "Yerine geçen",
  "settings.textNormalization.table.priority": "Öncelik",
  "settings.textNormalization.table.active": "Aktif",
  "settings.textNormalization.empty":
    "Henüz tanımlanmış bir metin normalizasyon kuralı yok.",
  "settings.textNormalization.status.active": "Aktif",
  "settings.textNormalization.status.inactive": "Pasif",

  "language.label": "Dil",
  "language.english": "İngilizce",
  "language.turkish": "Türkçe",

  "errors.generic.load": "Veriler yüklenirken bir hata oluştu.",
  "errors.documents.load": "Dokümanlar yüklenirken bir hata oluştu.",
  "errors.documents.upload": "Dosya(lar) yüklenirken bir hata oluştu.",
  "errors.documents.delete": "Doküman silinirken bir hata oluştu.",
  "errors.documents.reprocess":
    "Doküman yeniden işlenirken bir hata oluştu.",
  "errors.documents.deleteAll": "Tüm dokümanlar silinirken bir hata oluştu.",
  "errors.chunks.loadDocuments": "Dokümanlar yüklenirken bir hata oluştu.",
  "errors.chunks.loadChunks": "Parçalar yüklenirken bir hata oluştu.",
  "errors.chunks.search": "Parçalar aranırken bir hata oluştu.",
  "errors.settings.load": "Ayarlar yüklenirken bir hata oluştu.",
  "errors.settings.update": "Ayar güncellenirken bir hata oluştu.",
  "errors.regulations.load":
    "Regülasyon ayarları yüklenirken bir hata oluştu.",
  "errors.regulations.update":
    "Regülasyon kuralları güncellenirken bir hata oluştu.",
  "errors.regulations.test":
    "Kural test edilirken bir hata oluştu. Bu bir regex kuralı ise, desenin geçerli olduğundan emin olun.",
  "errors.regulations.save": "Kural kaydedilirken bir hata oluştu.",
  "errors.regulations.delete": "Kural silinirken bir hata oluştu.",
  "errors.textNormalization.load": "Metin normalizasyon kuralları yüklenemedi.",
  "errors.textNormalization.create": "Kural oluşturulamadı. Lütfen regex desenini kontrol edin.",
  "errors.textNormalization.delete": "Kural silinemedi.",
  "errors.preview.document":
    "Doküman önizlemesi yüklenirken bir hata oluştu.",
  "errors.preview.transcription":
    "Transkripsiyon önizlemesi yüklenirken bir hata oluştu.",

  "documents.upload.duplicates":
    'Zaten yüklenmiş dosyalar atlandı: {names}.',

  "uploader.title": "Dosyalarınızı buraya sürükleyip bırakın",
  "uploader.subtitle":
    "PDF, Word, Excel, görseller, ses dosyaları ve diğer desteklenen formatlar",
  "uploader.button": "Dosya seç",

  "documents.preview.schemaLoadError": "Tablo şeması yüklenemedi.",
  "documents.preview.schemaSaveError": "Şema değişiklikleri kaydedilemedi.",
  "documents.preview.sanitizedContent": "Anonimleştirilmiş içerik",
  "documents.preview.spreadsheetSchema": "Tablo şeması",
  "documents.preview.unsavedChanges": "Kaydedilmemiş değişiklikler",
  "documents.preview.loadingSchema": "Şema yükleniyor...",
  "documents.preview.noSchema": "Bu doküman için tablo şeması mevcut değil.",
  "documents.preview.noColumns": "Bu tablo için sütun tespit edilemedi.",
  "documents.preview.schemaInstruction": "Genel sütun etiketlerini anlamsal rollere eşleyin. Buraya ham kişisel veri girmekten kaçının.",
  "documents.preview.technicalLabel": "Teknik etiket",
  "documents.preview.semanticLabel": "Anlamsal etiket",
  "documents.preview.numeric": "Sayısal",
  "documents.preview.unsavedWarning": "Kaydedilmemiş değişiklikleriniz var.",
  "documents.preview.saving": "Kaydediliyor...",
  "documents.preview.saveSchema": "Şemayı kaydet",
  "documents.chunks": "parça",

  "preview.document.title": "Doküman Önizlemesi",
  "preview.document.loading": "Doküman önizlemesi yükleniyor…",
  "preview.document.empty":
    "Bu doküman için gösterilecek bir önizleme içeriği yok.",
  "preview.transcription.title": "Ses Transkripsiyonu",
  "preview.transcription.loading": "Transkripsiyon yükleniyor…",
  "preview.transcription.empty":
    "Henüz bu doküman için transkripsiyon metni yok.",
  "preview.close": "Kapat",

  "chat.output.label": "Çıktı:",
  "chat.output.tab.chat": "Sohbet",
  "chat.output.tab.json": "JSON",
  "chat.emptyState":
    "Başlamak için bir doküman seçip bir mesaj yazın. Yanıtlar kelime kelime akar.",
  "chat.input.placeholder": "Dokümanınız hakkında soru sorun…",
  "chat.button.stop": "Durdur",
  "chat.button.send": "Gönder",
  "chat.button.upload": "Doküman ekle",
  "chat.status.thinking": "Düşünüyor",
  "chat.copy": "Kopyala",
  "chat.copied": "Kopyalandı",
  "chat.localFallbackBadge": "Yanıt yerel model ile verildi (bulut kullanılamadı)",
  "chat.copyAnswer": "Cevabı kopyala",
  "chat.deanonBanner":
    "Yanıtlar cihazınızda yerel olarak de-anonimleştirilir. Cevaptaki yer tutucular yalnızca sizin cihazınızda özgün değerlerle değiştirilmiştir.",

  "chat.approval.rejected": "Bağlam reddedildi. LLM'e yanıt gönderilmedi.",
  "chat.approval.timeout": "Onay zaman aşımına uğradı (60s).",
  "chat.approval.rejectedDefault": "Reddedildi.",
  "chat.debug.fetchError": "Hata ayıklama bilgisi alınırken bir hata oluştu.",
  "chat.morePills": "+{count} daha",

  "chat.debug.title": "Buluta giden ve gelen veri",
  "chat.debug.button": "Buluta giden veriyi göster",
  "chat.debug.maskedPrompt": "Buluta giden istem (maskelenmiş)",
  "chat.debug.maskedAnswer": "Buluttan gelen yanıt (maskelenmiş)",
  "chat.debug.finalAnswer": "Yerelde işlenmiş ve gösterilen yanıt",

  "chat.documentSelector.hint":
    "Sorgulamak için en az bir doküman seçin. Yalnızca hazır dokümanlar listelenir.",
  "chat.documentSelector.empty":
    "Sohbet için hazır doküman yok. Önce doküman yükleyip işlemeniz gerekir.",

  "chat.json.title": "JSON çıktısı",
  "chat.json.invalid": "Geçersiz JSON",
  "chat.json.notFound": "Yanıtta JSON bulunamadı",
  "chat.json.structuredTitle": "Yapılandırılmış görünüm (markdown'dan):",
  "chat.json.empty": "Henüz içerik yok.",
  "chat.json.rawTitle": "Ham yanıt",

  "chat.approval.title": "Maskelemiş içeriği LLM'e göndermeden önce onaylayın",
  "chat.approval.regulations":
    "Bu istek şu regülasyonlar kapsamında işleniyor: {regs}.",
  "chat.approval.noRegulations":
    "Etkinleştirilmiş özel bir regülasyon yok.",
  "chat.approval.timeRemaining": "Kalan süre: {seconds}s",
  "chat.approval.maskedPrompt.title": "Maskelenmiş istem (anonimleştirilmiş)",
  "chat.approval.maskedPrompt.empty": "(boş)",
  "chat.approval.chunks.title": "Getirilen parçalar (düzenlenebilir)",
  "chat.approval.chunks.helper":
    "LLM'e göndermeden önce anonimleştirilmiş parça metnini düzenleyebilirsiniz.",
  "chat.approval.chunks.label": "Parça {index}",
  "chat.approval.chunks.page": "Sayfa {page}",
  "chat.approval.button.reject": "Reddet",
  "chat.approval.button.approve": "Onayla ve devam et",

  "chunks.error.save":
    "Bu parçada yapılan değişiklikler kaydedilemedi.",
  "chunks.error.delete":
    "Bu parça silinirken bir hata oluştu.",
  "chunks.confirm.delete":
    "Bu parçayı silmek istediğinizden emin misiniz?",
  "chunks.card.label": "Parça #{index}",
  "chunks.card.showLess": "Daha az göster",
  "chunks.card.showMore": "Daha fazla göster",
  "chunks.card.charCount": "{count} karakter",
  "chunks.card.lang": "Dil: {lang}",
  "chunks.card.regs": "Reg.: {regs}",
  "chunks.card.loadingChunks": "Parçalar yükleniyor…",
  "chunks.card.noChunks": "Bu doküman için parça yok.",

  "chunks.entity.detectedUnder":
    "Şu kapsamda tespit edildi: {regs}",
  "chunks.entity.placeholder": "Tespit edilen varlık yer tutucusu",

  "common.saving": "Kaydediliyor…",
  "common.save": "Kaydet",
  "common.cancel": "İptal",
  "common.edit": "Düzenle",
  "common.deleting": "Siliniyor…",
  "common.delete": "Sil",
  "common.close": "Kapat",

  "regulations.page.title": "Regülasyon Kuralları ve Özel Kurallar",
  "regulations.page.subtitle":
    "Yerleşik regülasyon paketlerini etkinleştirin ve kurumunuza özel tanıyıcılar tanımlayın.",
  "regulations.builtin.title": "Yerleşik Regülasyon Kuralları",
  "regulations.builtin.subtitle":
    "Küresel gizlilik regülasyonlarını açıp kapatın. Aktif regülasyonlar tek bir anonimleştirme politikasında birleştirilir.",
  "regulations.builtin.summary.active": "Aktif",
  "regulations.builtin.summary.entities": "Toplam varlık türü",
  "regulations.builtin.loading": "Regülasyon kuralları yükleniyor...",
  "regulations.builtin.entityCountSuffix": "varlık türünü kapsar.",
  "regulations.builtin.officialLink": "Resmi metni görüntüle",
  "regulations.builtin.viewEntities": "Varlık türlerini göster",
  "regulations.builtin.hideEntities": "Varlık türlerini gizle",
  "regulations.builtin.badge.builtin": "Yerleşik",
  "regulations.builtin.region": "Bölge",

  "regulations.desc.gdpr":
    "Avrupa Birliği ve EEA için kapsamlı veri koruma regülasyonu (GDPR).",
  "regulations.desc.ccpa":
    "Kaliforniya veri koruma ve gizlilik regülasyonu (CCPA).",
  "regulations.desc.hipaa":
    "Korunan sağlık bilgilerini (PHI) düzenleyen ABD regülasyonu (HIPAA).",
  "regulations.desc.lgpd":
    "Brezilya Genel Veri Koruma Kanunu (LGPD).",
  "regulations.desc.kvkk":
    "Türkiye Kişisel Verilerin Korunması Kanunu (KVKK).",

  "regulations.custom.title": "Özel Kurallar",
  "regulations.custom.subtitle":
    "Regex, anahtar kelime listeleri veya yerel LLM istemleriyle kurumunuza özel varlıklar tanımlayın. Özel kurallar yerleşik regülasyonlarla birleştirilir.",
  "regulations.custom.addButton": "Yeni Kural Ekle",
  "regulations.custom.loading": "Özel kurallar yükleniyor...",
  "regulations.custom.empty":
    'Henüz özel bir kural tanımlanmadı. İlk kuralınızı oluşturmak için "Yeni Kural Ekle"yi kullanın.',
  "regulations.custom.table.name": "Ad",
  "regulations.custom.table.entityType": "Varlık Türü",
  "regulations.custom.table.method": "Yöntem",
  "regulations.custom.table.placeholder": "Yer Tutucu",
  "regulations.custom.table.status": "Durum",
  "regulations.custom.table.actions": "İşlemler",
  "regulations.custom.method.regex": "Regex deseni",
  "regulations.custom.method.keyword": "Anahtar kelime listesi",
  "regulations.custom.method.llm": "LLM istemi",
  "regulations.custom.status.active": "Aktif",
  "regulations.custom.status.inactive": "Pasif",
  "regulations.custom.action.edit": "Düzenle",

  "regulations.panel.createTitle": "Yeni Özel Kural",
  "regulations.panel.editTitle": "Özel Kuralı Düzenle",
  "regulations.panel.description":
    "Varlık etiketini, tespit yöntemini ve bağlam kelimelerini tanımlayın. Kaydetmeden önce kuralı örnek metin üzerinde test edebilirsiniz.",
  "regulations.panel.close": "Kapat",
  "regulations.panel.field.ruleName": "Kural Adı",
  "regulations.panel.field.ruleName.placeholder": "Hasta Dosya Numarası",
  "regulations.panel.field.entityType": "Varlık Türü",
  "regulations.panel.field.entityType.placeholder": "PATIENT_FILE_NUMBER",
  "regulations.panel.field.entityType.helper":
    "Büyük harf ve alt çizgi ile ayrılmış varlık tanımlayıcısı.",
  "regulations.panel.field.placeholderLabel": "Yer Tutucu Etiketi",
  "regulations.panel.field.placeholderLabel.placeholder": "PATIENT_FILE",
  "regulations.panel.field.placeholderLabel.helper":
    "Yer tutucular bu etiketten üretilir, örneğin [PATIENT_FILE_1].",
  "regulations.panel.field.detectionMethod": "Tespit Yöntemi",
  "regulations.panel.method.regex.title": "Regex Deseni",
  "regulations.panel.method.regex.description": "Gelişmiş desenler",
  "regulations.panel.method.regex.placeholder": "[A-Z]{2}-\\d{4}",
  "regulations.panel.method.regex.helper":
    "Python regex ile uyumlu olmalıdır; backend, kaydetmeden önce deseni doğrular.",
  "regulations.panel.method.keyword.title": "Anahtar Kelime Listesi",
  "regulations.panel.method.keyword.description": "Sabit terimler",
  "regulations.panel.method.keyword.placeholder":
    "Acme Corp, GlobalTech, İç Proje X",
  "regulations.panel.method.keyword.helper":
    "Metinde geçmesi beklenen tam anahtar kelimeleri girin.",
  "regulations.panel.method.llm.title": "LLM İstemi",
  "regulations.panel.method.llm.description": "Ollama tabanlı",
  "regulations.panel.method.llm.placeholder":
    "Metinde geçen tüm maaş tutarlarını bul.",
  "regulations.panel.method.llm.helper":
    "Bu açıklama, Ollama üzerinden LLM tabanlı bir tanıyıcı oluşturmak için kullanılır.",
  "regulations.panel.field.contextWords": "Bağlam Kelimeleri (isteğe bağlı)",
  "regulations.panel.field.contextWords.placeholder":
    "hasta, dosya, ref, hesap",
  "regulations.panel.field.contextWords.helper":
    "Yakında göründüklerinde skoru yükseltmesi gereken yardımcı kelimeler (virgülle ayırın).",
  "regulations.panel.field.sample": "Test Örneği",
  "regulations.panel.field.sample.placeholder":
    "Kaydetmeden önce kuralınızı test etmek için buraya bir örnek metin yapıştırın.",
  "regulations.panel.ruleActive": "Kural aktif",
  "regulations.panel.button.delete": "Sil",
  "regulations.panel.button.deletePending": "Siliniyor...",
  "regulations.panel.button.test": "Kuralı Test Et",
  "regulations.panel.button.testPending": "Test ediliyor...",
  "regulations.panel.button.saveCreate": "Kaydet ve Aktifleştir",
  "regulations.panel.button.saveEdit": "Değişiklikleri Kaydet",
  "regulations.panel.button.savePending": "Kaydediliyor...",
  "regulations.panel.test.idle": "",
  "regulations.panel.test.pending": "Kural testi çalıştırılıyor...",
  "regulations.panel.test.noSample":
    "Test çalıştırmadan önce lütfen bir örnek metin girin.",
  "regulations.panel.test.missingRequired":
    "Test etmeden önce lütfen Ad, Varlık Türü ve Yer Tutucu Etiketi alanlarını doldurun.",
  "regulations.panel.test.noRuleId":
    "Kural kimliği çözülemedi; test çalıştırılamıyor.",
  "regulations.panel.test.noMatches": "Eşleşme bulunamadı.",
  "regulations.panel.test.noMatchesLlm":
    "LLM-istem tabanlı özel tanıyıcılar şu anda backend'de yer tutucu olarak uygulanmıştır, bu nedenle eşleşmeler dönmeyebilir.",
  "regulations.panel.test.successWithCount":
    "{count} eşleşme bulundu.",
  "regulations.panel.test.matchesTitle": "Test eşleşmeleri",
  "regulations.panel.save.missingRequired":
    "Kaydetmeden önce lütfen Ad, Varlık Türü ve Yer Tutucu Etiketi alanlarını doldurun.",

  "regulations.panel.match.scoreLabel": "skor",

  "regulations.panel.delete.error":
    "Kural silinirken bir hata oluştu.",

  "regulations.panel.test.error.generic":
    "Kural test edilirken bir hata oluştu. Bu bir regex kuralı ise, desenin geçerli olduğundan emin olun.",

  "regulations.panel.save.error":
    "Kural kaydedilirken bir hata oluştu.",

  "regulations.panel.toggle.aria": "Kuralı aktif/pasif yap",

  "regulations.nonPii.title": "Non-PII Kuralları (Gelişmiş)",
  "regulations.nonPii.subtitle":
    "Bazı span'ların (örneğin selamlamalar, boilerplate) PII olarak maskelenmemesi için gelişmiş kurallar. Bu liste yalnızca ileri kullanıcılara yöneliktir; çoğu senaryo için gerekmez.",
  "regulations.nonPii.loading": "Non-PII kuralları yükleniyor...",
  "regulations.nonPii.empty":
    "Şu anda tanımlı Non-PII kuralı yok. Sistem, veri odaklı varsayılan davranışla çalışmaya devam eder.",
  "regulations.nonPii.table.patternType": "Desen Türü",
  "regulations.nonPii.table.pattern": "Desen",
  "regulations.nonPii.table.languages": "Diller",
  "regulations.nonPii.table.entityTypes": "Varlık Türleri",
  "regulations.nonPii.table.minScore": "Min. Skor",
  "regulations.nonPii.table.status": "Durum",
  "regulations.nonPii.anyLanguage": "Tüm diller",
  "regulations.nonPii.anyEntity": "Tüm varlık türleri",

  "audit.title": "Denetim Kaydı",
  "audit.subtitle": "Gizlilikle ilgili olaylar: PII tespiti, de-anonimleştirme, regülasyon değişiklikleri.",
  "audit.loading": "Denetim olayları yükleniyor...",
  "audit.empty": "Kayıtlı denetim olayı yok.",
  "audit.column.time": "Zaman",
  "audit.column.eventType": "Olay Türü",
  "audit.column.documentId": "Doküman",
  "audit.column.sessionId": "Oturum",
  "audit.column.entityCount": "Varlıklar",
  "audit.column.regulations": "Regülasyonlar",
  "audit.column.entityTypes": "Varlık Türleri",
  "audit.filter.eventType": "Olay türü",
  "audit.filter.allEvents": "Tüm olaylar",
  "audit.paginationSummary": "Toplam {total} · sayfa {page} / {totalPages}",
  "audit.prevPage": "Önceki",
  "audit.nextPage": "Sonraki",
  "audit.eventType.pii_detected": "PII Tespit Edildi",
  "audit.eventType.deanonymization_performed": "De-anonimleştirme",
  "audit.eventType.document_uploaded": "Doküman Yüklendi",
  "audit.eventType.document_deleted": "Doküman Silindi",
  "audit.eventType.regulation_changed": "Regülasyon Değiştirildi",
  "errors.audit.load": "Denetim olayları yüklenirken bir hata oluştu."
};

