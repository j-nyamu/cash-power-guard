package com.example.electricity_management_system.sms;

import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestClient;

/**
 * Picks the SMS sender: Africa's Talking when {@code sms.africastalking.username} and
 * {@code sms.africastalking.api-key} are set, otherwise a development fallback that only logs.
 */
@Slf4j
@Configuration
@EnableConfigurationProperties(AfricasTalkingProperties.class)
public class SmsConfig {
    @Bean
    @ConditionalOnMissingBean(SmsSender.class)
    SmsSender smsSender(AfricasTalkingProperties props, RestClient.Builder restClientBuilder) {
        if (props.isConfigured()) {
            log.info("SMS gateway: Africa's Talking ({}) as {}", props.resolvedBaseUrl(), props.username());
            return new AfricasTalkingSmsSender(restClientBuilder, props);
        }
        log.warn("No SMS gateway configured: SMS messages (including USSD login codes) will only be logged.");
        return (phone, message) -> log.info("[SMS to {}] {}", phone, message);
    }
}
