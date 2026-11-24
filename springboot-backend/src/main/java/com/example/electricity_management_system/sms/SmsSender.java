package com.example.electricity_management_system.sms;

/** Sends an SMS. Replace the logging sender in {@link SmsConfig} with a real gateway (e.g. Africa's Talking) in production. */
public interface SmsSender {
    void send(String phone, String message);
}
