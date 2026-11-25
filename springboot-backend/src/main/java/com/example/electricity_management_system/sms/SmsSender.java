package com.example.electricity_management_system.sms;

/** Sends an SMS. The implementation is chosen in {@link SmsConfig}. */
public interface SmsSender {
    /**
     * @param phone   recipient in international format, e.g. +2507XXXXXXXX
     * @param message text to send
     * @throws SmsDeliveryException if the message could not be sent
     */
    void send(String phone, String message);
}
