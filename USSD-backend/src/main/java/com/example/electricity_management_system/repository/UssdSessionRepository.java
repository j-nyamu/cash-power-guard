package com.example.electricity_management_system.repository;

import com.example.electricity_management_system.ussd.UssdSessionModel;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.stereotype.Repository;

import java.time.LocalDateTime;

@Repository
public interface UssdSessionRepository extends JpaRepository<UssdSessionModel, String> {
    @Modifying
    @Query("delete from UssdSessionModel s where s.updatedAt < :cutoff")
    int deleteOlderThan(LocalDateTime cutoff);
}
