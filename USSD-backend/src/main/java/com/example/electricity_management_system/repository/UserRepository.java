package com.example.electricity_management_system.repository;

import com.example.electricity_management_system.model.UserModel;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Collection;
import java.util.Optional;

@Repository
public interface UserRepository extends JpaRepository<UserModel,Long> {
    UserModel findByEmail(String username);
    Optional<UserModel> findFirstByPhoneNumberInOrderByIdAsc(Collection<String> phoneNumbers);
    boolean existsByNationalID(String nationalID);
}
