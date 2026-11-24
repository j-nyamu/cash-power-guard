package com.example.electricity_management_system.sms;

import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Development fallback: writes SMS messages to the log instead of sending them.
 * It is only used when no other {@link SmsSender} bean is defined.
 */
@Slf4j
@Configuration
public class SmsConfig {
    @Bean
    @ConditionalOnMissingBean(SmsSender.class)
    SmsSender loggingSmsSender() {
        log.warn("No SMS gateway configured: SMS messages (including USSD login codes) will only be logged.");
        return (phone, message) -> log.info("[SMS to {}] {}", phone, message);
    }
}
