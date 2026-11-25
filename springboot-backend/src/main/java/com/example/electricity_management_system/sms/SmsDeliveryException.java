package com.example.electricity_management_system.sms;

/** Thrown when an SMS could not be handed to the gateway or the gateway rejected it. */
public class SmsDeliveryException extends RuntimeException {
    public SmsDeliveryException(String message) {
        super(message);
    }

    public SmsDeliveryException(String message, Throwable cause) {
        super(message, cause);
    }
}
