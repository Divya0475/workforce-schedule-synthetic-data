# Workforce Scheduling Synthetic Data Generator: Data Dictionary

This document details the schema specifications for all entities within the synthetic workforce scheduling dataset, including core operational tables, audit history logs, and derived reporting tables. For every entity, each attribute includes data type, nullability, permitted values, physical units, uniqueness constraints, foreign keys, and validation rules.

---

## Table of Contents
1. [Core Identifiers & Global Conventions](#1-core-identifiers--global-conventions)
2. [Entity Specifications (Source Tables)](#2-entity-specifications-source-tables)
   - [2.1 Team](#21-team)
   - [2.2 Skill](#22-skill)
   - [2.3 Employee](#23-employee)
   - [2.4 EmployeeSkill (Bridge Table)](#24-employeeskill-bridge-table)
   - [2.5 ShiftTemplate](#25-shifttemplate)
   - [2.6 HolidayCalendar](#26-holidaycalendar)
   - [2.7 CoverageRequirement](#27-coveragerequirement)
   - [2.8 ShiftAssignment](#28-shiftassignment)
   - [2.9 LeaveRequest](#29-leaverequest)
   - [2.10 LeaveRequestEvent (History Table)](#210-leaverequestevent-history-table)
   - [2.11 SwapRequest](#211-swaprequest)
   - [2.12 SwapRequestEvent (History Table)](#212-swaprequestevent-history-table)
   - [2.13 AttendanceEvent](#213-attendanceevent)
3. [Derived Analytics Tables (Computed from Source Records)](#3-derived-analytics-tables-computed-from-source-records)
   - [3.1 CoverageGap](#31-coveragegap)
   - [3.2 WeeklyHoursSummary](#32-weeklyhourssummary)
4. [Ground Truth Anomaly Metadata Specification](#4-ground-truth-anomaly-metadata-specification)
5. [Integrity & Cross-Table Validation Rules](#5-integrity--cross-table-validation-rules)

---

## 1. Core Identifiers & Global Conventions

All identifiers across tables follow strict zero-padded, immutable formatting to preserve traceability and simplify querying:

| Entity / Dimension | ID Format Template | Example ID | Description |
| :--- | :--- | :--- | :--- |
| **Employee** | `EMP-{seq:06d}` | `EMP-000001` | 6-digit zero-padded sequential integer (1 to 2,000) |
| **Team** | `TEAM-{seq:03d}` | `TEAM-001` | 3-digit zero-padded sequential integer (1 to 100) |
| **Skill** | `SKL-{code}` | `SKL-TRIAGE` | Unique uppercase semantic code |
| **ShiftTemplate** | `SFT-{code}` | `SFT-A` | Uppercase template letter ('A', 'B', 'C') |
| **Shift Slot / Instance** | `SHIFT-{YYYY-MM-DD}-TEAM{seq:03d}-{code}` | `SHIFT-2026-01-01-TEAM001-A` | Deliberate extension of brief's `SHIFT-YYYY-MM-DD-X` format |
| **ShiftAssignment** | `ASN-{YYYY}-{seq:06d}` | `ASN-2026-000001` | Year + 6-digit zero-padded sequence |
| **LeaveRequest** | `LVE-{YYYY}-{seq:06d}` | `LVE-2026-000001` | Year + 6-digit zero-padded sequence |
| **LeaveRequestEvent** | `LVE-EVT-{YYYY}-{seq:06d}` | `LVE-EVT-2026-000001` | Year + 6-digit zero-padded sequence |
| **SwapRequest** | `SWP-{YYYY}-{seq:06d}` | `SWP-2026-000001` | Year + 6-digit zero-padded sequence |
| **SwapRequestEvent** | `SWP-EVT-{YYYY}-{seq:06d}` | `SWP-EVT-2026-000001` | Year + 6-digit zero-padded sequence |
| **AttendanceEvent** | `ATT-{employee_id}-{timestamp}` | `ATT-EMP-000001-20260101T055800Z` | Employee ID + UTC compact ISO timestamp |
| **CoverageRequirement** | `COV-{YYYY}-{seq:06d}` | `COV-2026-000001` | Year + 6-digit zero-padded sequence |
| **HolidayCalendar** | `HOL-{YYYY}-{seq:03d}` | `HOL-2026-001` | Year + 3-digit zero-padded sequence |
| **CoverageGap (Derived)** | `GAP-{YYYY}-{seq:06d}` | `GAP-2026-000001` | Year + 6-digit zero-padded sequence |
| **WeeklyHoursSummary** | `WHS-{emp_id}-{YYYY}W{WW}` | `WHS-EMP-000001-2026W01` | Employee ID + ISO Year-Week string |
| **Anomaly Ground Truth** | `ANM-{seq:04d}` | `ANM-0001` | 4-digit zero-padded sequence for injected edge cases |

### Justification of Shift Instance ID: `SHIFT-YYYY-MM-DD-TEAMxxx-X`
The brief introduced the template `SHIFT-YYYY-MM-DD-X`, where `X` represents the shift template code (e.g., `A`, `B`, `C`). 
In an enterprise operation scaling to **100 teams** running concurrent shifts every day:
- If only `SHIFT-2026-01-01-A` were used, all 100 teams would collide on the exact same identifier.
- As a **deliberate architectural extension**, the format incorporates the team identifier: `SHIFT-YYYY-MM-DD-TEAM{team_id:03d}-{template_code}` (e.g., `SHIFT-2026-01-01-TEAM001-A`).
- This satisfies the brief's naming pattern while guaranteeing mathematical uniqueness across all $100 \times 90 \times 3 = 27,000$ shift slots.

- **Timezone**: All timestamps are formatted in ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`).
- **Dates**: Formatted as ISO 8601 calendar date (`YYYY-MM-DD`).
- **Times**: Formatted as 24-hour military time (`HH:MM:SS`).

---

## 2. Entity Specifications (Source Tables)

### 2.1 Team
Represents an operational organizational unit responsible for coverage within specific departments.

| Field Name | Type | Req/Opt | Allowed Values | Units | Uniqueness | Foreign Key | Validation Rule |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `team_id` | VARCHAR(16) | Required | Pattern: `^TEAM-\d{3}$` | N/A | **PK** | None | Must match `TEAM-[0-9]{3}` (001 to 100) |
| `name` | VARCHAR(64) | Required | Any non-empty string | N/A | Unique | None | Min length 3 characters |
| `department` | VARCHAR(64) | Required | `Emergency`, `Inpatient`, `Logistics`, `Customer Support` | N/A | Non-unique | None | Must exist in company dept list |
| `timezone` | VARCHAR(32) | Required | Valid IANA Timezone (`UTC`, `America/New_York`) | N/A | Non-unique | None | Valid timezone identifier |
| `is_active` | BOOLEAN | Required | `TRUE`, `FALSE` | N/A | Non-unique | None | Default `TRUE` |

---

### 2.2 Skill
Defines specialized capabilities or certifications required to satisfy shift coverage.

**Skill Taxonomy & Starter Profile Distribution**:
1. **Core Operational Skills**:
   - `SKL-TRIAGE`: Emergency Triage & Assessment (Clinical)
   - `SKL-ICU_CARE`: Intensive Care Operations (Clinical)
   - `SKL-PHARMACY`: Clinical Pharmacology (Clinical)
   - `SKL-DISPATCH`: Resource & Unit Dispatch (Logistical)
   - `SKL-SUPPLY_CHAIN`: Medical Supply Chain (Logistical)
   - `SKL-INTAKE`: Patient Intake & Admission (Administrative)
2. **Specialized Clinical Oncology (`SKL-ONCOLOGY`)**:
   - Clinical Oncology Protocols (Clinical).
   - In the starter profile, operational demand is assigned to Inpatient Operations (`TEAM-002`) on Shift A weekdays (`min_headcount = 1`), with qualified non-manager holders assigned to the team.
3. **Linguistic Certification (`SKL-BILINGUAL_ES`)**:
   - Bilingual Spanish (Linguistic).
   - **Secondary-Only Rule**: Must **NEVER** be assigned as a primary skill (`is_primary = FALSE` on 100% of rows).
   - Distribution: Orthogonally assigned to roughly **20–25% of employees** across all departments.
4. **Rare Specialist Skills with Zero Holders in Starter Profile**:
   - `SKL-TOXICOLOGY`: Clinical Toxicology (Poisoning and overdose protocol)
   - `SKL-HAZMAT`: Hazardous Material Logistics (Biohazard containment and waste dispatch)
   - `SKL-CRISIS_COUNSEL`: Crisis Counseling (Behavioral crisis de-escalation)
   - *Domain Modeling Note*: These 3 advanced niche certifications represent specialized enterprise catalog skills with zero operational demand and 0 assigned staff in the 50-person starter cohort. In the full 2,000-employee benchmark profile, they are staffed at low frequencies (<1%).

| Field Name | Type | Req/Opt | Allowed Values | Units | Uniqueness | Foreign Key | Validation Rule |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `skill_id` | VARCHAR(16) | Required | Pattern: `^SKL-[A-Z_]+$` | N/A | **PK** | None | Uppercase alphanumeric + underscores |
| `code` | VARCHAR(32) | Required | `TRIAGE`, `DISPATCH`, `ICU_CARE`, `PHARMACY`, `BILINGUAL_ES`, etc. | N/A | Unique | None | Uppercase code |
| `name` | VARCHAR(64) | Required | Standard readable string | N/A | Unique | None | Min length 3 |
| `category` | VARCHAR(32) | Required | `Clinical`, `Logistical`, `Linguistic`, `Administrative` | N/A | Non-unique | None | Standard taxonomy category |
| `description` | VARCHAR(255)| Optional | String or `NULL` | N/A | Non-unique | None | Max length 255 |

---

### 2.3 Employee
Represents workforce members available for scheduling, management, and shift assignments.

> [!IMPORTANT]
> **Manager Scheduling & Coverage Exclusion Rule**:
> Employees with `role == 'MANAGER'` are administrative team supervisors. They approve leave and swap requests, but are **NOT scheduled for shift assignments and are strictly EXCLUDED from operational coverage counts**.

| Field Name | Type | Req/Opt | Allowed Values | Units | Uniqueness | Foreign Key | Validation Rule |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `employee_id` | VARCHAR(16) | Required | Pattern: `^EMP-\d{6}$` | N/A | **PK** | None | Must match `EMP-[0-9]{6}` (000001 to 002000) |
| `team_id` | VARCHAR(16) | Required | Valid `team_id` | N/A | Non-unique | `Team(team_id)` | Must refer to active Team |
| `first_name` | VARCHAR(64) | Required | Alpha string | N/A | Non-unique | None | Non-blank |
| `last_name` | VARCHAR(64) | Required | Alpha string | N/A | Non-unique | None | Non-blank |
| `email` | VARCHAR(128)| Required | RFC 5322 format | N/A | Unique | None | Valid email regex |
| `role` | VARCHAR(32) | Required | `STAFF`, `SENIOR`, `LEAD`, `MANAGER` | N/A | Non-unique | None | Roles include `MANAGER` |
| `weekly_contract_hours` | DECIMAL(4,1) | Required | `20.0`, `32.0`, `40.0` | Hours/week | Non-unique | None | `weekly_contract_hours == fte * 40.0` |
| `fte` | DECIMAL(3,2) | Required | `0.50`, `0.80`, `1.00` | Ratio [0.0 - 1.0] | Non-unique | None | `0.1 <= fte <= 1.0` |
| `hire_date` | DATE | Required | `1990-01-01` to `2026-03-31` | YYYY-MM-DD | Non-unique | None | Must precede or equal schedule start |
| `termination_date` | DATE | Optional | Date or `NULL` | YYYY-MM-DD | Non-unique | None | If present, must be `> hire_date` |
| `is_active` | BOOLEAN | Required | `TRUE`, `FALSE` | N/A | Non-unique | None | `FALSE` if `termination_date` reached |

---

### 2.4 EmployeeSkill (Bridge Table)
Resolves many-to-many relationship between Employees and Skills with proficiency and credential tracking.

| Field Name | Type | Req/Opt | Allowed Values | Units | Uniqueness | Foreign Key | Validation Rule |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `employee_skill_id` | VARCHAR(24) | Required | Pattern: `^ESK-\d{6}$` | N/A | **PK** | None | Zero-padded sequential ID |
| `employee_id` | VARCHAR(16) | Required | Valid `employee_id` | N/A | Composite Unique (emp, skill) | `Employee(employee_id)` | Must refer to valid Employee |
| `skill_id` | VARCHAR(16) | Required | Valid `skill_id` | N/A | Composite Unique (emp, skill) | `Skill(skill_id)` | Must refer to valid Skill |
| `proficiency_level` | INTEGER | Required | `1`, `2`, `3`, `4`, `5` | Scale (1-5) | Non-unique | None | `1 <= proficiency_level <= 5` |
| `certified_date` | DATE | Required | Valid calendar date | YYYY-MM-DD | Non-unique | None | `certified_date <= CURRENT_DATE` |
| `expiry_date` | DATE | Optional | Date or `NULL` | YYYY-MM-DD | Non-unique | None | If present, must be `> certified_date` |
| `is_primary` | BOOLEAN | Required | `TRUE`, `FALSE` | N/A | Non-unique | None | Exactly 1 primary skill per employee |

---

### 2.5 ShiftTemplate
Defines the standard operational time windows, lengths, and break expectations for recurring shifts.

| Field Name | Type | Req/Opt | Allowed Values | Units | Uniqueness | Foreign Key | Validation Rule |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `shift_template_id` | VARCHAR(16) | Required | Pattern: `^SFT-[A-Z]$` | N/A | **PK** | None | Distinct letter template identifier |
| `template_code` | CHAR(1) | Required | `'A'`, `'B'`, `'C'` | N/A | Unique | None | Must match `[A-C]` |
| `name` | VARCHAR(32) | Required | `'Morning'`, `'Afternoon'`, `'Night'` | N/A | Unique | None | Non-blank name |
| `start_time` | TIME | Required | `06:00:00`, `14:00:00`, `22:00:00` | HH:MM:SS | Non-unique | None | Valid 24-hr time |
| `end_time` | TIME | Required | `14:00:00`, `22:00:00`, `06:00:00` | HH:MM:SS | Non-unique | None | Valid 24-hr time |
| `duration_hours` | DECIMAL(3,1) | Required | `8.0` | Hours | Non-unique | None | Calculated duration minus unpaid break |
| `unpaid_break_minutes`| INTEGER | Required | `30` | Minutes | Non-unique | None | Standard break duration (`>= 0`) |
| `is_overnight` | BOOLEAN | Required | `TRUE` (for C), `FALSE` (for A, B) | N/A | Non-unique | None | `TRUE` if `end_time < start_time` |

---

### 2.6 HolidayCalendar
Documents recognized public, federal, or statutory holidays.

**Operational Roles & Derived Date Joins**:
`HolidayCalendar` operates as a calendar-date reference table joined temporally via `calendar_date` (no rigid database foreign key constraint, allowing standard calendar-interval joins):
1. **Leave Hour Counting**: Statutory holidays (`is_statutory = TRUE`) falling within an approved leave window (`start_date <= calendar_date <= end_date`) do NOT consume an employee's annual leave quota.
2. **Coverage Target Adjustment**: Teams adjust coverage demand on statutory holidays (`calendar_date == effective_date`), adopting weekend/holiday demand tiers.
3. **Overtime & Premium Rate Flag**: Active shifts worked on statutory holidays (`calendar_date == shift_date`) flag attendance punches for premium compensation.

| Field Name | Type | Req/Opt | Allowed Values | Units | Uniqueness | Foreign Key | Validation Rule |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `holiday_id` | VARCHAR(16) | Required | Pattern: `^HOL-\d{4}-\d{3}$` | N/A | **PK** | None | Year + 3-digit sequence |
| `calendar_date` | DATE | Required | Valid calendar date | YYYY-MM-DD | Unique per jurisdiction | None | Valid date within horizon |
| `name` | VARCHAR(64) | Required | e.g. `'New Year\'s Day'`, `'President\'s Day'` | N/A | Non-unique | None | Non-blank string |
| `jurisdiction` | VARCHAR(32) | Required | `'FEDERAL'`, `'REGIONAL'` | N/A | Non-unique | None | Standard jurisdiction domain |
| `is_statutory` | BOOLEAN | Required | `TRUE`, `FALSE` | N/A | Non-unique | None | Flags mandatory holiday leave exemption & premium pay |

---

### 2.7 CoverageRequirement
Defines minimum and target qualified headcount needed for every scheduled team, shift, and skill.

**Headcount Semantics & Slot Granularity**:
- **Distinct Slot Representation**: Each row in `CoverageRequirement` represents a **distinct, individual headcount slot** demanded for a specific `(effective_date, team_id, shift_template_id, skill_id)` combination.
- **Hard Minimum Floor (`min_headcount`)**: Represents the absolute hard statutory, safety, or clinical minimum staffing requirement. If the number of active, qualified scheduled staff falls below `min_headcount`, a critical `CoverageGap` violation record is emitted.
- **Soft Target Buffer (`target_headcount`)**: Represents the optimal/desired operational staffing target (`target_headcount >= min_headcount`). Operating between `min_headcount` and `target_headcount` is standard operating procedure and does **NOT** constitute a violation or gap.

| Field Name | Type | Req/Opt | Allowed Values | Units | Uniqueness | Foreign Key | Validation Rule |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `coverage_id` | VARCHAR(24) | Required | Pattern: `^COV-\d{4}-\d{6}$` | N/A | **PK** | None | Year + 6-digit sequence |
| `effective_date` | DATE | Required | Valid calendar date | YYYY-MM-DD | Composite Unique (date, team, shift, skill) | None | Date within scheduling horizon |
| `team_id` | VARCHAR(16) | Required | Valid `team_id` | N/A | Composite Unique | `Team(team_id)` | Active team reference |
| `shift_template_id` | VARCHAR(16) | Required | Valid `shift_template_id` | N/A | Composite Unique | `ShiftTemplate(shift_template_id)` | Valid shift template |
| `skill_id` | VARCHAR(16) | Required | Valid `skill_id` | N/A | Composite Unique | `Skill(skill_id)` | Specific skill demanded |
| `min_headcount` | INTEGER | Required | `>= 1` (typically 1 to 4) | Staff count | Non-unique | None | Hard minimum floor (`min_headcount >= 1`) |
| `target_headcount`| INTEGER | Required | `>= min_headcount` | Staff count | Non-unique | None | Soft optimal target (`target_headcount >= min_headcount`) |

---

### 2.8 ShiftAssignment
Represents an employee's scheduled commitment to work a shift slot.

> [!IMPORTANT]
> **Versioning & Non-Destructive Swap Invariant**:
> `ShiftAssignment` versioning **never deletes or reassigns an existing row**.
> - When a shift swap is approved and applied:
>   - The old row is updated **strictly in two fields**: `assignment_status` $\leftarrow$ `'SWAPPED_OUT'` and `effective_to` $\leftarrow \text{applied\_at}$.
>   - The `employee_id` on the old row remains permanently preserved.
>   - A new row is inserted for the receiving employee with `assignment_status = 'ACTIVE'`, `effective_from = applied_at`, `effective_to = NULL`, linking back to `swap_request_id` and `replaces_assignment_id`.

| Field Name | Type | Req/Opt | Allowed Values | Units | Uniqueness | Foreign Key | Validation Rule |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `assignment_id` | VARCHAR(24) | Required | Pattern: `^ASN-\d{4}-\d{6}$` | N/A | **PK** | None | Zero-padded sequential assignment |
| `shift_instance_id`| VARCHAR(32) | Required | Pattern: `^SHIFT-\d{4}-\d{2}-\d{2}-TEAM\d{3}-[A-Z]$` | N/A | Non-unique | None | Unambiguous across all 100 teams |
| `employee_id` | VARCHAR(16) | Required | Valid `employee_id` | N/A | Non-unique | `Employee(employee_id)` | Employee assigned (cannot be a MANAGER) |
| `team_id` | VARCHAR(16) | Required | Valid `team_id` | N/A | Non-unique | `Team(team_id)` | Team delivering coverage |
| `shift_template_id`| VARCHAR(16)| Required | Valid `shift_template_id` | N/A | Non-unique | `ShiftTemplate(shift_template_id)` | Inherits start/end time window |
| `shift_date` | DATE | Required | Valid calendar date | YYYY-MM-DD | Non-unique | None | Date of shift commencement |
| `scheduled_start`| TIMESTAMP | Required | ISO 8601 UTC timestamp | UTC | Non-unique | None | Computed from `shift_date` + `start_time` |
| `scheduled_end` | TIMESTAMP | Required | ISO 8601 UTC timestamp | UTC | Non-unique | None | Computed from `shift_date` + `end_time` (+1 day if overnight) |
| `duration_hours`| DECIMAL(3,1) | Required | `8.0` | Hours | Non-unique | None | Scheduled duration |
| `assigned_role` | VARCHAR(32) | Required | `STAFF`, `SENIOR`, `LEAD` | N/A | Non-unique | None | Employee role at assignment time (never MANAGER) |
| `assignment_status`| VARCHAR(16)| Required | `ACTIVE`, `SWAPPED_OUT` | N/A | Non-unique | None | `ACTIVE` for current commitments; `SWAPPED_OUT` for superseded history |
| `effective_from` | TIMESTAMP | Required | ISO 8601 UTC timestamp | UTC | Non-unique | None | Timestamp assignment row became valid |
| `effective_to` | TIMESTAMP | Optional | ISO 8601 UTC timestamp or `NULL`| UTC | Non-unique | None | `NULL` while `ACTIVE`; closed with timestamp upon `SWAPPED_OUT` |
| `swap_request_id` | VARCHAR(24) | Optional | Valid `swap_request_id` or `NULL` | N/A | Non-unique | `SwapRequest(swap_request_id)` | `NULL` for original baseline rows; populated on swap replacement rows |
| `replaces_assignment_id`| VARCHAR(24)| Optional| Valid `assignment_id` or `NULL` | N/A | Non-unique | `ShiftAssignment(assignment_id)` | `NULL` for baseline; points to superseded row for swap replacements |
| `fulfills_skill_id` | VARCHAR(16) | Optional | Valid `skill_id` or `NULL` | N/A | Non-unique | `Skill(skill_id)` | Skill slot satisfied by assignment; `NULL` if filling general headcount buffer |
| `is_holiday_premium`| BOOLEAN | Required | `TRUE`, `FALSE` | N/A | Non-unique | None | `TRUE` if shift_date is a statutory holiday |

---

### 2.9 LeaveRequest
Captures formal employee requests for absence.

**Status Synchronization Rule**:
`LeaveRequest.current_status` **must equal** the `to_status` of the latest `LeaveRequestEvent`.

| Field Name | Type | Req/Opt | Allowed Values | Units | Uniqueness | Foreign Key | Validation Rule |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `leave_request_id`| VARCHAR(24) | Required | Pattern: `^LVE-\d{4}-\d{6}$` | N/A | **PK** | None | Zero-padded sequential ID |
| `employee_id` | VARCHAR(16) | Required | Valid `employee_id` | N/A | Non-unique | `Employee(employee_id)` | Active employee requestor |
| `leave_type` | VARCHAR(16) | Required | `ANNUAL`, `SICK`, `UNPAID`, `PARENTAL`, `BEREAVEMENT` | N/A | Non-unique | None | Must match `config.leave_rules.types` |
| `start_date` | DATE | Required | Valid calendar date | YYYY-MM-DD | Non-unique | None | `start_date <= end_date` |
| `end_date` | DATE | Required | Valid calendar date | YYYY-MM-DD | Non-unique | None | `end_date >= start_date` |
| `requested_hours`| DECIMAL(4,1)| Required | Positive decimal (`>= 8.0`) | Hours | Non-unique | None | `requested_hours > 0` |
| `current_status` | VARCHAR(16) | Required | `SUBMITTED`, `APPROVED`, `REJECTED`, `CANCELLED` | N/A | Non-unique | None | **Must equal latest `LeaveRequestEvent.to_status`** |
| `submitted_at` | TIMESTAMP | Required | ISO 8601 UTC timestamp | UTC | Non-unique | None | `submitted_at <= start_date` (except SICK) |
| `decided_at` | TIMESTAMP | Optional | ISO 8601 UTC timestamp or `NULL` | UTC | Non-unique | None | `decided_at >= submitted_at` |
| `decision_reason` | VARCHAR(255)| Optional | Narrative text or `NULL` | N/A | Non-unique | None | Free text explanation |

---

### 2.10 LeaveRequestEvent (History Table)
Immutable audit event store tracking the complete lifecycle history of every leave request.

**Actor Type & Manager Rules**:
- `actor_type` indicates whether the transition was initiated by a human or automated engine: `EMPLOYEE` or `SYSTEM`.
- If `actor_type == 'EMPLOYEE'`, `actor_id` **must be a valid `employee_id` where `role == 'MANAGER'` and `team_id == requester.team_id`**.
- If `actor_type == 'SYSTEM'`, `actor_id` is **`NULL`**.

| Field Name | Type | Req/Opt | Allowed Values | Units | Uniqueness | Foreign Key | Validation Rule |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `leave_event_id` | VARCHAR(24) | Required | Pattern: `^LVE-EVT-\d{4}-\d{6}$` | N/A | **PK** | None | Zero-padded event ID |
| `leave_request_id`| VARCHAR(24) | Required | Valid `leave_request_id` | N/A | Non-unique | `LeaveRequest(leave_request_id)` | Foreign key to parent request |
| `from_status` | VARCHAR(16) | Optional | `NULL`, `SUBMITTED`, `APPROVED` | N/A | Non-unique | None | `NULL` on initial creation |
| `to_status` | VARCHAR(16) | Required | `SUBMITTED`, `APPROVED`, `REJECTED`, `CANCELLED` | N/A | Non-unique | None | Valid state transition |
| `event_timestamp`| TIMESTAMP | Required | ISO 8601 UTC timestamp | UTC | Non-unique | None | Chronologically increasing per request |
| `actor_type` | VARCHAR(16) | Required | `EMPLOYEE`, `SYSTEM` | N/A | Non-unique | None | Originator type |
| `actor_id` | VARCHAR(16) | Optional | Valid `employee_id` or `NULL` | N/A | Non-unique | `Employee(employee_id)` | Required for EMPLOYEE; `NULL` for SYSTEM |
| `reason` | VARCHAR(255)| Optional | Explanation or justification | N/A | Non-unique | None | Free text explanation |

---

### 2.11 SwapRequest
Manages shift exchange proposals between two employees.

**Mandatory Swap Rules**:
1. **Same Team**: Requestor and target must belong to the **same operational team** (`requestor.team_id == target.team_id`).
2. **Valid Skills**: Both parties must hold certified active skills required by the shift they are receiving.
3. **Rest & Overtime Compliance**: The trade must not cause an 11-hour rest violation or push either employee $> 48.0$ weekly hours.
4. **Manager Approval**: Must be approved by a Manager of the team (`manager_employee_id.role == 'MANAGER'`).
5. **Status Sync**: `current_status` **must equal** the `to_status` of the latest `SwapRequestEvent`.

| Field Name | Type | Req/Opt | Allowed Values | Units | Uniqueness | Foreign Key | Validation Rule |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `swap_request_id` | VARCHAR(24) | Required | Pattern: `^SWP-\d{4}-\d{6}$` | N/A | **PK** | None | Zero-padded sequential ID |
| `requestor_employee_id` | VARCHAR(16) | Required | Valid `employee_id` | N/A | Non-unique | `Employee(employee_id)` | Initiating employee |
| `requestor_assignment_id`| VARCHAR(24) | Required | Valid `assignment_id` | N/A | Non-unique | `ShiftAssignment(assignment_id)` | Initiator's assignment |
| `target_employee_id` | VARCHAR(16) | Required | Valid `employee_id` | N/A | Non-unique | `Employee(employee_id)` | Partner (`team_id == requestor.team_id`) |
| `target_assignment_id` | VARCHAR(24) | Required | Valid `assignment_id` | N/A | Non-unique | `ShiftAssignment(assignment_id)` | Partner's assignment |
| `current_status` | VARCHAR(16) | Required | `REQUESTED`, `APPROVED`, `REJECTED`, `APPLIED` | N/A | Non-unique | None | **Must equal latest `SwapRequestEvent.to_status`** |
| `manager_employee_id` | VARCHAR(16) | Optional | Valid `employee_id` or `NULL` | N/A | Non-unique | `Employee(employee_id)` | Team manager (`role == 'MANAGER'`) |
| `requested_at` | TIMESTAMP | Required | ISO 8601 UTC timestamp | UTC | Non-unique | None | Must precede earliest shift start |
| `decided_at` | TIMESTAMP | Optional | ISO 8601 UTC timestamp or `NULL` | UTC | Non-unique | None | `decided_at >= requested_at` |
| `applied_at` | TIMESTAMP | Optional | ISO 8601 UTC timestamp or `NULL` | UTC | Non-unique | None | `applied_at >= decided_at` |
| `rejection_reason` | VARCHAR(255)| Optional | Narrative text or `NULL` | N/A | Non-unique | None | Narrative explanation |

---

### 2.12 SwapRequestEvent (History Table)
Immutable audit log tracking all transitions in a swap request lifecycle.

| Field Name | Type | Req/Opt | Allowed Values | Units | Uniqueness | Foreign Key | Validation Rule |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `swap_event_id` | VARCHAR(24) | Required | Pattern: `^SWP-EVT-\d{4}-\d{6}$` | N/A | **PK** | None | Zero-padded event ID |
| `swap_request_id`| VARCHAR(24) | Required | Valid `swap_request_id` | N/A | Non-unique | `SwapRequest(swap_request_id)` | Foreign key to parent swap |
| `from_status` | VARCHAR(16) | Optional | `NULL`, `REQUESTED`, `APPROVED` | N/A | Non-unique | None | `NULL` on initial creation |
| `to_status` | VARCHAR(16) | Required | `REQUESTED`, `APPROVED`, `REJECTED`, `APPLIED` | N/A | Non-unique | None | Permitted lifecycle transition |
| `event_timestamp`| TIMESTAMP | Required | ISO 8601 UTC timestamp | UTC | Non-unique | None | Chronologically increasing per swap |
| `actor_type` | VARCHAR(16) | Required | `EMPLOYEE`, `SYSTEM` | N/A | Non-unique | None | Originator type |
| `actor_id` | VARCHAR(16) | Optional | Valid `employee_id` or `NULL` | N/A | Non-unique | `Employee(employee_id)` | Required for EMPLOYEE; `NULL` for SYSTEM |
| `notes` | VARCHAR(255)| Optional | Transition comment | N/A | Non-unique | None | Audit notes |

---

### 2.13 AttendanceEvent
Captures real-time timeclock events (clock-in, clock-out) with variance analysis against scheduled shifts.

**Important Relationship Constraint**:
`AttendanceEvent.assignment_id` **links only to `ACTIVE` `ShiftAssignment` records** (never to `SWAPPED_OUT` assignments).

| Field Name | Type | Req/Opt | Allowed Values | Units | Uniqueness | Foreign Key | Validation Rule |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `attendance_event_id` | VARCHAR(64) | Required | Pattern: `^ATT-EMP-\d{6}-\d{8}T\d{6}Z$` | N/A | **PK** | None | Formatted: `ATT-<emp_id>-<compact_timestamp>` |
| `employee_id` | VARCHAR(16) | Required | Valid `employee_id` | N/A | Non-unique | `Employee(employee_id)` | Employee recording attendance |
| `assignment_id` | VARCHAR(24) | Optional | Valid `assignment_id` or `NULL` | N/A | Non-unique | `ShiftAssignment(assignment_id)` | **Must reference an `ACTIVE` assignment** |
| `event_type` | VARCHAR(16) | Required | `CLOCK_IN`, `CLOCK_OUT` | N/A | Non-unique | None | Strict punch state domain |
| `event_timestamp` | TIMESTAMP | Required | ISO 8601 UTC timestamp | UTC | Non-unique | None | Realized physical punch timestamp |
| `source` | VARCHAR(16) | Required | `BIOMETRIC`, `BADGE_READER`, `MOBILE_APP`, `MANUAL_OVERRIDE` | N/A | Non-unique | None | Hardware/software terminal source |
| `variance_minutes` | INTEGER | Required | `-120` to `+240` | Minutes | Non-unique | None | `event_timestamp - scheduled_time` |
| `is_exception` | BOOLEAN | Required | `TRUE`, `FALSE` | N/A | Non-unique | None | `TRUE` if `abs(variance_minutes) > threshold` |
| `exception_type` | VARCHAR(32) | Optional | `NO_SHOW`, `LATE`, `EARLY_LEAVE`, `OVERTIME`, `NULL` | N/A | Non-unique | None | Populated if `is_exception == TRUE` |

---

## 3. Derived Analytics Tables (Computed from Source Records)

Derived tables are synthesized downstream by aggregating source entities. They do not maintain physical FK references from mutable operational tables, but compute deterministic analytical projections.

### 3.1 CoverageGap
Computed by comparing `CoverageRequirement` against active `ShiftAssignment` records cross-referenced with `EmployeeSkill`. Maintains a direct `coverage_id` foreign key back to the requirement row it evaluates.

$$\text{gap\_headcount} = \max\left(0, \; \text{required\_headcount} - \sum_{e \in \text{ActiveAssigned}(d, T, S)} \mathbb{I}(e \text{ holds skill } K)\right)$$

| Field Name | Type | Req/Opt | Allowed Values | Units | Uniqueness | Foreign Key | Description / Derivation Rule |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `gap_id` | VARCHAR(24) | Required | Pattern: `^GAP-\d{4}-\d{6}$` | N/A | **PK** | None | Zero-padded sequential gap identifier |
| `coverage_id` | VARCHAR(24) | Required | Valid `coverage_id` | N/A | Unique | `CoverageRequirement(coverage_id)` | **Direct foreign key to evaluated requirement** |
| `effective_date` | DATE | Required | Valid calendar date | YYYY-MM-DD | Non-unique | None | Date of coverage evaluation |
| `team_id` | VARCHAR(16) | Required | Valid `team_id` | N/A | Non-unique | `Team(team_id)` | Operational team evaluated |
| `shift_template_id`| VARCHAR(16)| Required | Valid `shift_template_id` | N/A | Non-unique | `ShiftTemplate(shift_template_id)` | Shift slot evaluated (A, B, C) |
| `skill_id` | VARCHAR(16) | Required | Valid `skill_id` | N/A | Non-unique | `Skill(skill_id)` | Skill evaluated |
| `required_headcount`| INTEGER | Required | `>= 1` | Headcount | Non-unique | None | Demanded `min_headcount` from `CoverageRequirement` |
| `scheduled_headcount`| INTEGER | Required | `>= 0` | Headcount | Non-unique | None | Actual active scheduled staff possessing `skill_id` |
| `gap_headcount` | INTEGER | Required | `>= 0` | Deficit | Non-unique | None | `MAX(0, required_headcount - scheduled_headcount)` |
| `target_shortfall`| INTEGER | Required | `>= 0` | Deficit | Non-unique | None | `MAX(0, target_headcount - scheduled_headcount)` |
| `reason` | VARCHAR(255)| Required | Narrative text | N/A | Non-unique | None | Diagnostic explanation (e.g., 'Fully staffed', 'Skill pinch', 'Sick leave absence') |
| `is_critical` | BOOLEAN | Required | `TRUE`, `FALSE` | N/A | Non-unique | None | `TRUE` if `gap_headcount > 0` |
| `computed_at` | TIMESTAMP | Required | ISO 8601 UTC timestamp | UTC | Non-unique | None | Timestamp of analytical computation |

---

### 3.2 WeeklyHoursSummary
Computed by aggregating active `ShiftAssignment` records across each ISO calendar week (Monday through Sunday) per employee, joined with contractual data from `Employee`.

**Overnight Shift & Partial Week Rules**:
1. **Overnight Shifts**: An overnight shift (Shift C: 22:00 to 06:00 $d+1$) counts **strictly toward the ISO week of its start date (`shift_date`)**.
2. **Partial Weeks (`is_partial_week`)**:
   - The planning horizon (2026-01-01 to 2026-03-31) begins on Thursday (2026-W01: Thu–Sun = 4 days) and ends on Tuesday (2026-W14: Mon–Tue = 2 days).
   - `is_partial_week` is `TRUE` for W01 and W14; `FALSE` for complete weeks W02 through W13.
   - Statutory overtime caps ($\le 48.0$ hours) **apply strictly to full weeks only (`is_partial_week == FALSE`)**.

| Field Name | Type | Req/Opt | Allowed Values | Units | Uniqueness | Foreign Key | Description / Derivation Rule |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `summary_id` | VARCHAR(32) | Required | Pattern: `^WHS-EMP-\d{6}-\d{4}W\d{2}$` | N/A | **PK** | None | Formatted: `WHS-<emp_id>-<YYYY>W<WW>` |
| `employee_id` | VARCHAR(16) | Required | Valid `employee_id` | N/A | Composite Unique | `Employee(employee_id)` | Employee summarized |
| `iso_year` | INTEGER | Required | `2026` | Year | Composite Unique | None | ISO 8601 calendar year |
| `iso_week` | INTEGER | Required | `1` to `14` | Week Number | Composite Unique | None | ISO 8601 calendar week number (01 to 53) |
| `is_partial_week`| BOOLEAN | Required | `TRUE`, `FALSE` | N/A | Non-unique | None | `TRUE` if week is truncated by horizon bounds (W01, W14) |
| `contract_hours` | DECIMAL(4,1) | Required | `20.0`, `32.0`, `40.0` | Hours | Non-unique | None | `Employee.weekly_contract_hours` |
| `scheduled_hours`| DECIMAL(4,1) | Required | `>= 0.0` | Hours | Non-unique | None | $\sum \text{duration\_hours}$ for `ACTIVE` shifts starting in week |
| `regular_hours` | DECIMAL(4,1) | Required | `>= 0.0` | Hours | Non-unique | None | `MIN(scheduled_hours, contract_hours)` |
| `overtime_hours`| DECIMAL(4,1) | Required | `>= 0.0` | Hours | Non-unique | None | `MAX(0.0, scheduled_hours - contract_hours)` |
| `exceeds_overtime_cap`| BOOLEAN | Required | `TRUE`, `FALSE` | N/A | Non-unique | None | `TRUE` if `scheduled_hours > 48.0` on full weeks (`is_partial_week == FALSE`) |
| `computed_at` | TIMESTAMP | Required | ISO 8601 UTC timestamp | UTC | Non-unique | None | Timestamp of analytical reconciliation |

---

## 4. Ground Truth Anomaly Metadata Specification

The ground truth dataset is stored in **`ground_truth.csv`** and **`ground_truth.json`**.
> [!IMPORTANT]
> **Strict Isolation Guarantee**:
> The ground truth file is an auxiliary evaluation artifact generated exclusively to audit and benchmark anomaly detection algorithms. It is **strictly prohibited** from ever being consumed as an input feature by the schedule generator, any training model, or the runtime validation suite (`validate.py`).

| Field Name | Type | Req/Opt | Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `anomaly_id` | VARCHAR(16) | Required | Pattern: `^ANM-\d{4}$` | Unique identifier for the injected anomaly |
| `anomaly_type` | VARCHAR(32) | Required | Enum (see below) | Categorical classification of the synthetic edge case |
| `table_name` | VARCHAR(32) | Required | `ShiftAssignment`, `SwapRequest`, `LeaveRequest` | Target table where the dirty mutation occurred |
| `record_id` | VARCHAR(24) | Required | Valid PK of target table | Primary key of the corrupted record |
| `field_name` | VARCHAR(32) | Required | Attribute name (e.g., `assignment_status`, `current_status`) | Specific field modified to induce the anomaly |
| `expected_value` | VARCHAR(255)| Optional | String representation | The valid value under standard business constraints |
| `actual_value` | VARCHAR(255)| Required | String representation | The mutated corrupt value injected into the dataset |
| `description` | VARCHAR(255)| Required | Free text narrative | Concise diagnostic explanation of the business violation |
| `injected_at` | TIMESTAMP | Required | ISO 8601 UTC timestamp | Audit timestamp when the anomaly was synthesized |

### Supported Anomaly Types (`anomaly_type`)
1. **`DOUBLE_BOOKED_EMPLOYEE`**: Single employee has two active assignments on overlapping shifts on the same calendar date.
2. **`APPROVED_LEAVE_OVERLAPPING_SHIFT`**: An employee has an `ACTIVE` shift assignment that directly overlaps with an `APPROVED` leave request date.
3. **`REQUIRED_SKILL_ABSENT`**: An active shift assignment fails to satisfy mandatory minimum skill coverage requirements for that shift slot.
4. **`OVERTIME_CAP_EXCEEDED`**: An employee is assigned total active shift hours exceeding 48.0 hours in a full ISO calendar week (Mon–Sun).
5. **`SWAP_APPLIED_WITHOUT_APPROVAL`**: A shift swap is marked `APPLIED` and executed on assignments without prior manager approval (or in contradiction to a `REJECTED` state).

---

## 5. Integrity & Cross-Table Validation Rules

1. **Consecutive Days Limit**:
   $$\sum_{d = t}^{t+6} \mathbb{I}(\text{ACTIVE ShiftAssignment exists for } d) \le 6 \quad \forall t$$
   An employee must have at least one day without an `ACTIVE` shift assignment in any rolling 7-day window.

2. **Rest Period Constraint**:
   $$\text{scheduled\_start}_{i+1} - \text{scheduled\_end}_i \ge 11 \text{ hours} \quad \forall \text{ adjacent ACTIVE assignments } (i, i+1)$$
   The interval between the scheduled conclusion of active shift $i$ and scheduled commencement of active shift $i+1$ must be $\ge 11.0$ hours.

3. **Weekly Hours & Overtime Cap**:
   $$\text{Total ACTIVE Hours} \le 48.0 \quad \text{per full ISO calendar week (where } is\_partial\_week == \text{FALSE)}.$$
   Enforced via `WeeklyHoursSummary.exceeds_overtime_cap == FALSE`. Overnight shifts count toward the week of their `shift_date`.

4. **Coverage Integrity**:
   For any $(d, T, S, K)$ representing date $d$, team $T$, shift template $S$, and skill $K$:
   $$\sum_{e \in \text{ActiveAssigned}(d, T, S)} \mathbb{I}(e \text{ holds skill } K) \ge \text{CoverageRequirement.min\_headcount}(d, T, S, K)$$
   Managers are excluded from `ActiveAssigned`. Enforced via `CoverageGap.is_critical == FALSE` across all slots in the clean baseline.

5. **Lifecycle Status Synchronization Invariant**:
   - For every `LeaveRequest` record:
     $$\text{LeaveRequest.current\_status} == \text{Latest}(\text{LeaveRequestEvent.to\_status})$$
   - For every `SwapRequest` record:
     $$\text{SwapRequest.current\_status} == \text{Latest}(\text{SwapRequestEvent.to\_status})$$

6. **Manager Authorization & System Actor Rules**:
   - When `actor_type == 'EMPLOYEE'`: `actor_id` must reference an Employee where `role == 'MANAGER'` and `team_id == requester.team_id`.
   - When `actor_type == 'SYSTEM'`: `actor_id` is `NULL`.

7. **ShiftAssignment Non-Destructive Versioning**:
   - Original assignment rows are never deleted and their `employee_id` is never reassigned.
   - When a swap is applied:
     - Old rows have `assignment_status` $\leftarrow$ `'SWAPPED_OUT'` and `effective_to` $\leftarrow \text{applied\_at}$.
     - Replacement rows have `assignment_status` $\leftarrow$ `'ACTIVE'`, `effective_from` $\leftarrow \text{applied\_at}$, `effective_to` $\leftarrow \text{NULL}$, linking to `swap_request_id` and `replaces_assignment_id`.

8. **Swap Eligibility Invariants**:
   - Both employees must belong to the **same operational team** (`requestor.team_id == target.team_id`).
   - Both employees must possess certified active skills required by the received shifts.
   - Both post-swap schedules must satisfy 11h rest and 48h weekly overtime limits.

9. **Attendance vs. Active Schedule Alignment**:
   - Every `AttendanceEvent` with a non-null `assignment_id` must reference an `ACTIVE` `ShiftAssignment`.
   - Exception types must strictly conform to `NO_SHOW`, `LATE`, `EARLY_LEAVE`, and `OVERTIME`.
   - Punches must not exist for shift assignments where the employee had an `APPROVED` leave request covering that date.
