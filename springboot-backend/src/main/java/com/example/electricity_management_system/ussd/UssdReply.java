package com.example.electricity_management_system.ussd;

/**
 * A USSD screen. Aggregators such as Africa's Talking expect plain text that starts with
 * "CON " (keep the session open and wait for input) or "END " (show this and close).
 */
public record UssdReply(boolean isEnd, String message) {
    public static UssdReply con(String message) {
        return new UssdReply(false, message);
    }

    public static UssdReply end(String message) {
        return new UssdReply(true, message);
    }

    public String toWire() {
        return (isEnd ? "END " : "CON ") + message;
    }
}
