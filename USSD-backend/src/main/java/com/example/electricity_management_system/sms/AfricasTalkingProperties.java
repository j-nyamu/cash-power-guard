package com.example.electricity_management_system.sms;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Africa's Talking SMS settings (prefix {@code sms.africastalking}).
 *
 * @param username Africa's Talking app username; "sandbox" sends through the sandbox API
 * @param apiKey   API key from the Africa's Talking dashboard; empty disables the gateway
 * @param senderId optional registered sender ID / short code; empty uses the default
 * @param baseUrl  optional API base URL override; empty picks live or sandbox from the username
 */
@ConfigurationProperties(prefix = "sms.africastalking")
public record AfricasTalkingProperties(String username, String apiKey, String senderId, String baseUrl) {
    static final String LIVE_URL = "https://api.africastalking.com";
    static final String SANDBOX_URL = "https://api.sandbox.africastalking.com";

    public boolean isConfigured() {
        return apiKey != null && !apiKey.isBlank() && username != null && !username.isBlank();
    }

    public String resolvedBaseUrl() {
        if (baseUrl != null && !baseUrl.isBlank()) {
            return baseUrl;
        }
        return "sandbox".equalsIgnoreCase(username) ? SANDBOX_URL : LIVE_URL;
    }
}
