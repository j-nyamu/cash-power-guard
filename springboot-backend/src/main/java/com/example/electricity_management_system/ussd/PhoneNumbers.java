package com.example.electricity_management_system.ussd;

import java.util.List;

/** Rwandan MSISDN helpers. Aggregators send +2507XXXXXXXX; users may have typed 07XXXXXXXX on the web. */
public final class PhoneNumbers {
    private PhoneNumbers() {
    }

    /** Returns +2507XXXXXXXX, or null when the number is not a Rwandan mobile number. */
    public static String normalize(String raw) {
        if (raw == null) {
            return null;
        }
        String digits = raw.replaceAll("[^0-9]", "");
        if (digits.startsWith("250") && digits.length() == 12) {
            digits = digits.substring(3);
        } else if (digits.startsWith("0") && digits.length() == 10) {
            digits = digits.substring(1);
        }
        return digits.matches("7[0-9]{8}") ? "+250" + digits : null;
    }

    /** Every spelling of a normalized number that may already be stored in users.phone_number. */
    public static List<String> variants(String normalized) {
        String local = normalized.substring(4);
        return List.of(normalized, "250" + local, "0" + local, local);
    }
}
