package com.example.electricity_management_system.ussd;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.LocalDateTime;

/** One USSD dialogue, keyed by the session id the aggregator sends with every callback. */
@Entity
@Table(name = "ussd_sessions", indexes = @Index(columnList = "updatedAt"))
@Getter
@Setter
@NoArgsConstructor
public class UssdSessionModel {
    @Id
    @Column(length = 64)
    private String sessionId;

    @Column(nullable = false, length = 15)
    private String phone;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 40)
    private UssdState state;

    /** Set once the caller has registered or entered a correct SMS code. */
    private Long userId;

    /** Small JSON map of in-progress values (pending code hash, chosen meter, amount, ...). */
    @Column(columnDefinition = "TEXT")
    private String data;

    @Column(nullable = false)
    private LocalDateTime updatedAt;

    public UssdSessionModel(String sessionId, String phone) {
        this.sessionId = sessionId;
        this.phone = phone;
    }
}
