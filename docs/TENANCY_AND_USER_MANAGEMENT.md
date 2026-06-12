# Tenancy and User Management

Health Assistant is designed to be "Home-First, Clinic-Ready." This document explains how the system handles multiple users, organizational hierarchies, and identity linking.

## 1. The Hierarchy Model

Health Assistant uses a three-tier hierarchy to manage data isolation and clinical context:

### Tier 1: Tenant (The Installation/Group)
The **Tenant** is the absolute boundary for data isolation. 
- **Home Use**: A Tenant represents the entire family or a single installation.
- **Clinic Use**: A Tenant represents a Medical Group or a SaaS customer.
- **Isolation**: Users in Tenant A can *never* see data in Tenant B.

### Tier 2: Organization (Household or Facility)
An **Organization** represents a physical or logical grouping within a Tenant.
- **Household**: In home setups, the system automatically creates a "Default Household" organization. This serves as the primary container for family members.
- **Clinic/Hospital**: Represents a specific branch or facility.
- **Org Types**: `HOUSEHOLD`, `CLINIC`, `HOSPITAL`, `DEPARTMENT`, `PROVIDER_GROUP`.

### Tier 3: Departments (Clinical Units)
Organizations can be recursive. A **Department** is simply an Organization that is "part of" another Organization.
- **Usage**: Use departments to group examinations (e.g., "Cardiology", "Dental").
- **Clinical Tracking**: Doctors can be associated with multiple departments, allowing them to track their work across the facility.

---

## 2. Roles and Permissions

Health Assistant implements a dual-scope Role-Based Access Control (RBAC) system.

| Role | Scope | Description |
| :--- | :--- | :--- |
| **SYSTEM_ADMIN** | Global | The system owner. Can manage all tenants, global AI configurations, and system-wide settings. The first user registered in a new installation is automatically promoted to this role. |
| **ADMIN** | Tenant | The Tenant owner (e.g., Head of Household or Clinic Manager). Can manage users and organizations within their own tenant. |
| **MANAGER** | Tenant | Administrative staff or senior caregivers. Can view all data in the tenant and manage clinical records. |
| **USER** | Tenant | Standard user. Access is typically restricted to their own records unless granted wider permissions. |

---

## 3. Identity Linking

A unique feature of Health Assistant is the separation and linking of **Identity** (who logs in) and **Clinical Records** (the data).

### Why Link?
In a household, the person who manages the app (the User) is often also a patient (the Patient record) or a caregiver (the Doctor record). Linking allows the system to:
- Automatically show "My Dashboard" when you log in.
- Associate login accounts with professional licenses for doctors.

### How it Works:
- **`DoctorModel`** and **`Patient`** models both contain an optional `user_id`.
- When a `user_id` is present, the clinical profile is "owned" by that login account.
- **Home Setup**: Usually, you will create a Patient record for yourself and link it to your User account.

### Unlinking:
- Links can be removed at any time from the **User Detail** page.
- Unlinking removes the connection but **preserves the clinical data**. The Patient or Doctor record remains in the database, just without an owner.

---

## 4. Smart Patient Context

To ensure data security and a smooth user experience, Health Assistant uses a "Smart Context" engine:

1. **Automatic Context**: If a User is linked to exactly one Patient record, that patient is automatically selected as the "active context" upon login.
2. **Single-Patient Default**: In new home installations with only one patient, that patient is selected by default.
3. **Secure Switching**: If an Admin manages multiple patients, they must manually select the context. The system will never show data from a random patient.
4. **Session Cleanup**: Logging out explicitly wipes the patient context from the browser to prevent data residue for the next user.

---

## 5. Workflows

### Home Setup (Zero-Config)
1. **Register**: The first user registers via `/auth/register`. 
2. **Auto-Provision**: The system detects this is the first user and:
   - Assigns them the `SYSTEM_ADMIN` role.
   - Creates a new `Tenant` automatically.
   - Creates a "Default Household" `Organization` automatically.
3. **Ready**: The user can immediately start adding family members as Patients.

### Clinical Setup
1. **Tenant Admin**: Registers and is assigned to a specific Tenant.
2. **Create Departments**: The Admin creates child Organizations of type `DEPARTMENT`.
3. **Invite Staff**: Admin creates User accounts for doctors and staff, assigning them to the relevant Departments.
4. **Link Doctors**: Clinical `Doctor` records are created and linked to the staff's `User` accounts via `user_id`.

---

## 6. CLI Administration

For system administrators, a command-line script is provided to manage the initial setup and promote users.

### Create or Promote System Admin
To create a new system administrator or promote an existing user:
```bash
python backend/scripts/create_system_admin.py --email admin@example.com --password "password"
```

**Arguments:**
- `--email`: The email address for the account.
- `--password`: The password (only used if creating a new user).
- `--tenant`: Optional name for the initial tenant if one needs to be created.
