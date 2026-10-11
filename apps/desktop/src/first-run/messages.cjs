// @ts-check
"use strict";

// First-run screen text in English and Arabic. `{product}` comes from packages/config/product.json.
// Shared by the page (via the preload) and the tests that check both languages have every key.

/** @type {Record<"en" | "ar", Record<string, string>>} */
const messages = {
  en: {
    title: "Set up {product}",
    intro: "Choose what this computer does.",
    otherLanguage: "العربية",
    mainTitle: "Main PC",
    mainBody:
      "Keeps this branch's business data. Choose this on one computer that stays on during working hours. Windows will ask for administrator approval.",
    mainName: "Name of this computer",
    mainDefaultName: "Main PC",
    mainButton: "Set up as main PC",
    mainWorking: "Setting up the database. This takes about a minute…",
    officeTitle: "Office PC",
    officeBody:
      "Connects to the main PC over the office network. On the main PC, open Settings → This PC and choose “Connect an office PC” to see the code.",
    officeAddress: "Main PC address",
    officeCode: "Pairing code",
    officeButton: "Connect",
    officeWorking: "Connecting…",
    "error.uac_cancelled": "Setup needs administrator approval. Try again and choose Yes.",
    "error.setup_failed": "Setup did not finish. Details are in {log}.",
    "error.invalid_address": "Enter an address such as 192.168.1.20.",
    "error.missing_code": "Enter the pairing code shown on the main PC.",
    "error.wrong_code": "The code does not match the one on the main PC.",
    "error.pairing_not_active": "Pairing is not open on the main PC. Show a new code there.",
    "error.pairing_unavailable": "Office network access is turned off on the main PC.",
    "error.unreachable":
      "Cannot reach the main PC. Check the address, that it is switched on, and that office network access is on.",
    "error.not_smartbiz": "That address is not a {product} main PC.",
    "error.unknown": "Something went wrong. Please try again.",
    "update.rolledBack":
      "The update could not be applied to this PC's data, so the previous version was restored and is running. Details: {log}",
    "update.failed": "The local server could not be updated. Details: {log}",
  },
  ar: {
    title: "إعداد {product}",
    intro: "اختر دور هذا الحاسوب.",
    otherLanguage: "English",
    mainTitle: "الحاسوب الرئيسي",
    mainBody:
      "يحفظ بيانات هذا الفرع. اختر هذا على حاسوب واحد يبقى يعمل خلال ساعات العمل. سيطلب Windows موافقة المسؤول.",
    mainName: "اسم هذا الحاسوب",
    mainDefaultName: "الحاسوب الرئيسي",
    mainButton: "إعداد كحاسوب رئيسي",
    mainWorking: "جارٍ إعداد قاعدة البيانات. يستغرق ذلك دقيقة تقريباً…",
    officeTitle: "حاسوب مكتبي",
    officeBody:
      "يتصل بالحاسوب الرئيسي عبر شبكة المكتب. على الحاسوب الرئيسي افتح الإعدادات ← هذا الحاسوب واختر «ربط حاسوب مكتبي» لعرض الرمز.",
    officeAddress: "عنوان الحاسوب الرئيسي",
    officeCode: "رمز الربط",
    officeButton: "اتصال",
    officeWorking: "جارٍ الاتصال…",
    "error.uac_cancelled": "يتطلب الإعداد موافقة المسؤول. حاول مرة أخرى واختر «نعم».",
    "error.setup_failed": "لم يكتمل الإعداد. التفاصيل في {log}.",
    "error.invalid_address": "أدخل عنواناً مثل 192.168.1.20.",
    "error.missing_code": "أدخل رمز الربط المعروض على الحاسوب الرئيسي.",
    "error.wrong_code": "الرمز لا يطابق الرمز المعروض على الحاسوب الرئيسي.",
    "error.pairing_not_active": "الربط غير مفتوح على الحاسوب الرئيسي. اعرض رمزاً جديداً هناك.",
    "error.pairing_unavailable": "الوصول عبر شبكة المكتب متوقف على الحاسوب الرئيسي.",
    "error.unreachable":
      "تعذّر الوصول إلى الحاسوب الرئيسي. تحقّق من العنوان وأنه قيد التشغيل وأن الوصول عبر شبكة المكتب مفعّل.",
    "error.not_smartbiz": "هذا العنوان ليس حاسوباً رئيسياً لـ {product}.",
    "error.unknown": "حدث خطأ ما. يرجى المحاولة مرة أخرى.",
    "update.rolledBack":
      "تعذّر تطبيق التحديث على بيانات هذا الحاسوب، لذلك أُعيد الإصدار السابق وهو يعمل الآن. التفاصيل: {log}",
    "update.failed": "تعذّر تحديث الخادم المحلي. التفاصيل: {log}",
  },
};

module.exports = { messages };
