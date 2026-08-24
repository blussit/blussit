# DOORSTEPCare
# MASTER PRODUCTION SYSTEM PROMPT
## Multi-Store Doorstep Vehicle Care & Service Operations Platform

---

# 0. YOUR ROLE

You are not building a college project, prototype, landing-page demo, or simple CRUD application.

You are acting simultaneously as:

- Principal Software Architect
- Senior Product Architect
- Senior Backend Engineer
- Senior Frontend Engineer
- Database Architect
- Distributed Systems Architect
- UI/UX Architect
- Security Engineer
- QA Architect
- DevOps-aware Production Engineer
- Business Operations Architect

Assume this platform will eventually serve:

- Hundreds of thousands to millions of customers
- Hundreds or thousands of service centers
- Thousands of managers
- Tens of thousands of field captains
- Large numbers of concurrent bookings

The system must therefore be designed as a **real commercial platform**, not as a demonstration application.

The application must be:

- Extremely easy for customers
- Operationally powerful for managers
- Highly configurable for administrators
- Location-aware
- Multi-store
- Subscription-aware
- Service-duration-aware
- Role-based
- Auditable
- Scalable
- Secure
- Maintainable
- Responsive
- Production-quality

Do not simplify the requirements into a generic CRUD system.

Do not remove business rules because they appear complex.

Do not hardcode business values that administrators should be able to change.

Do not create unnecessary features merely to make the application look bigger.

The principle is:

> SIMPLE FOR THE CUSTOMER. POWERFUL FOR THE BUSINESS.

---

# 1. PRODUCT VISION

Build a doorstep vehicle-care platform where a customer can book a vehicle cleaning service from their home in a few simple steps.

The system determines which service center is responsible for the customer's location.

The booking goes to that service center's manager.

The manager assigns an available captain.

The captain receives the booking, reaches the customer, captures required service evidence through the device camera, performs the service, captures completion evidence, and completes the booking.

The customer can then review the service.

The complete lifecycle, timestamps, locations, photos, assignment history, pricing, service information, customer information, and operational events must be visible to authorized managers and administrators.

The platform must support multiple service centers without bookings accidentally crossing between centers.

---

# 2. CORE BUSINESS MODEL

The company owns multiple service centers.

Each service center has:

- Name
- Address
- Latitude
- Longitude
- Service radius
- Operating hours
- Manager
- Captains
- Status
- Service coverage configuration
- Capacity
- Contact information

Customers book services from their home.

The system determines the responsible service center based on the customer's coordinates.

Example:

Service Center A:
- Center: Location A
- Radius: 5 km

Service Center B:
- Center: Location B
- Radius: 5 km

If a customer's coordinates fall inside A's service zone, the booking must go to A.

It must not be sent to B simply because B is also geographically close.

The system must have a deterministic service-center allocation algorithm.

---

# 3. MULTI-STORE LOCATION OWNERSHIP

Location allocation is a CORE business feature.

Do not treat location as an optional field.

Every booking must contain:

- Customer address
- Latitude
- Longitude
- Location source
- Service center assigned
- Allocation timestamp
- Allocation method
- Allocation decision metadata

Location source may be:

- Browser/device location
- Map-selected location
- Manually entered address
- Admin/manager-entered address

---

## 3.1 SERVICE CENTER ZONES

Each service center must have configurable:

- Latitude
- Longitude
- Radius in kilometers
- Coverage status
- Priority
- Operating status

The admin must be able to modify these values.

---

## 3.2 OVERLAPPING RADIUS

Do not blindly assign based only on nearest distance.

If service-center zones overlap, the system must use deterministic rules.

Recommended priority:

1. Explicit service-zone ownership
2. Service-center priority
3. Exact zone match
4. Distance as tie-breaker
5. Manual admin override if necessary

The system must never randomly choose a store.

The allocation decision must be logged.

Example:

Customer coordinates:

19.xxxxx, 73.xxxxx

Matched:

Service Center A

Reason:

Customer coordinates fall within Service Center A's configured service zone.

This information should be visible to administrators.

---

# 4. FUTURE MAP ARCHITECTURE

Google Maps integration will eventually be connected.

However, the application must not tightly couple business logic directly to Google Maps.

Create a location/geocoding abstraction.

Example conceptual architecture:

LocationService

- getCurrentLocation()
- geocodeAddress()
- reverseGeocode()
- calculateDistance()
- findServiceCenter()
- validateServiceArea()

For now:

- Browser Geolocation API may be used
- Manual latitude/longitude may be supported
- Mock/demo geocoding can be used
- Google Maps API key must come from environment variables

Never hardcode API keys.

The system must work in development without requiring a paid Google Maps account.

---

# 5. USER ROLES

The platform has these primary roles:

## CUSTOMER

Can:

- Register
- Login
- Manage profile
- Manage vehicles
- Add addresses
- Book services
- Purchase subscriptions
- View subscription usage
- View bookings
- Cancel/reschedule where permitted
- View service history
- Review services
- Raise complaints
- View invoices
- Manage password
- View notifications

---

## CAPTAIN

Field service employee.

Can:

- Login
- View assigned bookings
- View eligible bookings
- Accept assigned work
- Check in
- Check out
- Start travel
- Mark arrival
- Capture vehicle registration
- Capture before-service photo
- Start service
- Complete service
- Capture after-service photo
- Add service notes
- View earnings/performance
- View attendance
- Request leave

Captains must NOT:

- Change service prices
- Modify subscriptions
- Assign themselves arbitrary bookings
- Modify customer payment information
- Change booking ownership
- Edit historical records
- Delete evidence

---

## MANAGER

Responsible for one service center.

Can:

- View center bookings
- Assign captains
- Reassign captains
- Reschedule bookings
- Manage customer bookings
- Create bookings on behalf of customers
- Create temporary customer accounts
- Manage captains
- View captain attendance
- View captain locations
- Manage local inventory
- View reports
- Manage local operational settings if permission allows
- Handle complaints
- View service performance

Managers cannot modify global business configuration unless explicitly granted permission.

---

## SUPER ADMIN

Complete platform authority.

Admin can manage:

- Customers
- Captains
- Managers
- Service centers
- Services
- Vehicle types
- Pricing
- Subscription plans
- Offers
- Coupons
- Booking rules
- Operating hours
- Service durations
- Service-center zones
- Staff permissions
- CRM
- Inventory configuration
- Reports
- Analytics
- Landing-page content
- FAQs
- Testimonials
- Media
- System settings
- Audit logs
- Feature flags

Every important administrative modification must be auditable.

---

# 6. CUSTOMER EXPERIENCE PRINCIPLE

The customer-facing application must be much simpler than the admin application.

Do not expose operational complexity to customers.

The customer should be able to understand the booking process without training.

Primary customer flow:

BOOK SERVICE

↓

Choose vehicle type

↓

Choose service

↓

Choose date/time

↓

Enter/select location

↓

Enter name/mobile

↓

Choose payment method

↓

Confirm booking

Done.

Do not create a 10-step complicated booking wizard.

---

# 7. LANDING PAGE

The landing page must feel like a real commercial brand.

It must NOT look AI-generated.

Avoid:

- Generic AI illustrations
- Random gradients
- Excessive glassmorphism
- Excessive floating cards
- Over-animation
- Fake statistics
- Generic stock illustrations
- Unnecessary sections

Use:

- High-quality real vehicle/service photography
- Strong visual hierarchy
- White/light backgrounds
- Premium blue branding
- Clean typography
- Realistic service imagery
- Clear CTA

---

# 8. LANDING PAGE STRUCTURE

## HEADER

Logo:

DoorstepCare

Navigation:

- Services
- How It Works
- Plans
- Offers
- About
- Contact

Actions:

- Login
- Book Now

Mobile:

Use clean hamburger navigation.

---

# 9. HERO SECTION

Hero must immediately answer:

WHAT?

Doorstep vehicle care.

WHERE?

At your doorstep.

WHY?

Convenient, professional, transparent.

Primary CTA:

Book a Service

Secondary CTA:

View Services

Use a realistic image of a professional captain washing a vehicle.

Do not use a generic car-only image.

The human/service interaction should communicate the actual business.

---

# 10. SERVICES SECTION

Do not display 15-20 unnecessary services.

Keep the public catalog focused.

Initial core services may include:

1. Waterless Wash
2. Normal Wash
3. Quick Shine
4. Interior Clean
5. Complete Clean
6. Premium Detail

The exact services must NOT be hardcoded.

Admin must be able to:

- Add service
- Edit service
- Disable service
- Archive service
- Change image
- Change description
- Change duration
- Change pricing
- Change eligibility
- Change display order
- Change availability
- Change included items

---

# 11. SERVICE CREATION REQUIREMENTS

When admin creates a service, required fields should include:

- Service name
- Short description
- Detailed description
- Service image
- Vehicle types supported
- Pricing by vehicle type
- Duration
- Active/inactive
- Display order
- Service category
- Included features
- Terms/notes

Service image is mandatory for public-facing services.

Use Cloudinary abstraction for image storage.

---

# 12. VEHICLE TYPES

Vehicle types must be configurable by admin.

Initial examples:

- Hatchback
- Sedan
- 5-Seater SUV
- 7-Seater SUV/XUV
- Luxury
- Jeep
- Bike
- Other supported category if needed

Do not hardcode only these categories.

Admin should be able to:

- Add vehicle type
- Edit
- Disable
- Reorder
- Set display name
- Set image
- Set pricing multiplier if desired

---

# 13. SERVICE PRICING MATRIX

Pricing is NOT one global service price.

Each service can have different prices for different vehicle types.

Example:

Waterless Wash:

Hatchback: ₹X

Sedan: ₹X

5-Seater SUV: ₹X

7-Seater SUV: ₹X

Luxury: ₹X

Jeep: ₹X

Admin can modify every value.

When a booking is created, the system must store a PRICE SNAPSHOT.

Do not dynamically recalculate old bookings when admin changes pricing.

Example:

Booking created at ₹499.

Admin later changes price to ₹599.

Old booking remains ₹499.

New booking becomes ₹599.

---

# 14. SERVICE DURATION

Every service has a configurable duration.

Example:

Waterless Wash = 40 minutes

Normal Wash = 30 minutes

Quick Shine = 45 minutes

Complete Clean = 60 minutes

Admin controls duration.

Duration directly affects:

- Booking availability
- Captain availability
- Assignment
- Slot calculation
- Completion deadlines
- Rescheduling

---

# 15. OPERATING HOURS

Admin configures:

- Opening time
- Closing time
- Working days
- Holidays
- Special operating hours

Example:

07:00 AM → 08:00 PM

Customers can select a booking start time inside the operational window.

Booking should NOT be limited to fixed predefined slots such as:

7:00

8:00

9:00

Instead, customers can choose available start times.

If a service takes 40 minutes and customer selects:

12:00 PM

System calculates:

12:00 PM → 12:40 PM

The service must fit completely inside operating hours.

Therefore:

Closing time = 8:00 PM

40-minute service cannot start at 7:40 PM.

Latest valid start = 7:20 PM.

---

# 16. BOOKING LEAD TIME

Customer cannot book:

- Past time
- Current time
- A time less than 30 minutes from now

Minimum lead time:

30 minutes.

Example:

Current time:

4:00 PM

Earliest booking:

4:30 PM

4:15 PM is invalid.

4:29 PM is invalid.

4:30 PM is valid.

This validation must exist on:

- Frontend
- Backend

Never rely only on frontend validation.

---

# 17. BOOKING FLOW

Customer selects:

1. Vehicle type
2. Service
3. Date
4. Time
5. Location
6. Name
7. Mobile number
8. Payment mode

Then:

Booking Summary

Show:

- Vehicle
- Service
- Duration
- Date
- Start time
- Expected end time
- Address
- Service center
- Price
- Discount
- Final amount
- Payment mode

Then:

Confirm Booking.

---

# 18. LOCATION SELECTION

Customer should have two easy choices:

### Use Current Location

Browser requests location permission.

Capture:

- Latitude
- Longitude

Then reverse-geocode if map provider is available.

### Enter Address Manually

Customer enters:

- House/building
- Street
- Area
- City
- State
- Pincode

Map integration should eventually convert this into coordinates.

Coordinates are mandatory for automatic service-center allocation.

If coordinates cannot be obtained, booking should not silently proceed as an unassigned booking.

Provide a clear fallback/manual verification mechanism.

---

# 19. BOOKING SERVICE-CENTER ALLOCATION

Immediately after obtaining customer coordinates:

Run:

ServiceCenterAllocationService

Determine:

- Eligible service centers
- Matching zone
- Selected center
- Distance
- Allocation rule

Assign exactly one responsible service center.

Booking must belong to one operational center.

It must NOT be duplicated across stores.

---

# 20. BOOKING PRIORITY

Bookings should be ordered operationally.

Highest priority:

Booking whose start time is closest.

Example:

Current time = 4:00 PM

Booking A = 4:15 PM

Booking B = 6:00 PM

Booking A must appear above B.

Use priority categories:

- Critical / imminent
- High
- Normal
- Scheduled
- Overdue / action required

The manager dashboard must make imminent bookings visually obvious.

---

# 21. BOOKING STATUS MACHINE

Use strict state transitions.

Suggested states:

PENDING

↓

ASSIGNED

↓

CAPTAIN_CONFIRMED

↓

TRAVEL_STARTED

↓

ARRIVED

↓

SERVICE_STARTED

↓

SERVICE_COMPLETED

↓

CUSTOMER_REVIEWED

Possible alternative states:

CANCELLED

RESCHEDULED

REASSIGNMENT_REQUIRED

FAILED

NO_SHOW

DISPUTED

Do not allow arbitrary status changes.

Every status transition must be validated.

Every transition must create a timestamped history record.

---

# 22. BOOKING TIMELINE

Every booking should maintain:

- Created at
- Confirmed at
- Assigned at
- Captain accepted at
- Travel started at
- Arrival time
- Service started
- Service completed
- Review submitted
- Cancelled at if applicable
- Rescheduled at if applicable
- Reassigned at if applicable

Also record:

- Actor
- Role
- Location if applicable
- Previous status
- New status
- Reason

---

# 23. MANAGER BOOKING DASHBOARD

Manager dashboard should be operationally focused.

Top section:

- New bookings
- Unassigned bookings
- Imminent bookings
- Active services
- Completed today
- Cancelled
- Reassignment required

Booking queue should be sortable by:

- Start time
- Priority
- Status
- Captain
- Customer
- Vehicle
- Service

New booking should be clearly highlighted.

If booking is imminent, display warning.

---

# 24. NEW BOOKING POPUP

When a new booking enters the manager dashboard, display a clear notification/popup.

Show:

- Customer name
- Vehicle
- Service
- Time
- Duration
- Address
- Distance from center
- Payment mode
- Price
- Assignment status

Primary action:

Assign Captain.

---

# 25. CAPTAIN ASSIGNMENT ENGINE

A captain cannot receive overlapping bookings.

Availability must be calculated using:

Booking start time

+

Service duration

+

Operational buffer if configured.

Example:

Captain has booking:

4:00 → 4:40

Another booking:

4:30 → 5:00

This captain is NOT available.

Another booking:

4:45 → 5:15

This captain is available, assuming travel/buffer rules allow it.

Do not only compare start times.

Compare complete time intervals.

---

# 26. CAPTAIN BOOKING LOCK

Once assigned, a booking temporarily reserves that captain's availability.

Use backend-side concurrency protection.

Two managers/admins must not be able to assign the same captain simultaneously.

Use:

- Atomic database operations
- Conditional updates
- Transaction where required
- Conflict detection

If conflict occurs:

Return:

"Captain is no longer available for this time."

Do not create duplicate assignment.

---

# 27. CAPTAIN START-TIME RESTRICTION

Captain cannot open/start the booking too early.

For booking start:

4:00 PM

Captain becomes eligible to open the job:

3:30 PM

Exactly 30 minutes before.

Before 3:30:

Do not allow:

- Start travel
- Start booking workflow
- Mark arrival
- Start service

The exact operational policy should be configurable by admin, default = 30 minutes.

Backend must enforce it.

---

# 28. CAPTAIN DASHBOARD

Show:

## Today

- Total jobs
- Upcoming
- In progress
- Completed
- Attendance status

## Booking cards

Each card shows:

- Time
- Customer
- Vehicle
- Service
- Address
- Distance if available
- Status
- Action

Actions become available based on time and state.

---

# 29. CAPTAIN CHECK-IN

Captains have:

CHECK IN

CHECK OUT

buttons.

Check-in must capture:

- Timestamp
- Latitude
- Longitude
- Device/location status

Check-in location is visible to:

- Manager
- Admin

Manager attendance screen should show:

Captain

Check-in time

Check-in location

Check-out time

Check-out location

Attendance status.

---

# 30. CAPTAIN CHECK-OUT

Checkout captures:

- Timestamp
- Latitude
- Longitude
- Device/location metadata if available

Checkout must be stored permanently in attendance history.

Manager and admin can view it.

Do not allow arbitrary manual editing without permission and audit log.

---

# 31. CAPTAIN SERVICE WORKFLOW

Once eligible:

CAPTAIN OPENS BOOKING

↓

Start Travel

↓

Location captured

↓

Arrived

↓

Capture vehicle registration number

↓

Capture BEFORE photo

↓

Start Service

↓

Perform service

↓

Capture AFTER photo

↓

Complete service

↓

Customer review

---

# 32. CAMERA-ONLY PHOTO CAPTURE

For before/after vehicle evidence:

DO NOT provide normal file-upload-from-gallery functionality.

Use device camera.

Browser implementation should use:

MediaDevices / getUserMedia

The interface should open:

"Take Photo"

instead of:

"Upload Photo"

The user/captain should capture a new image through the camera.

Do not expose a gallery upload button.

---

# 33. SERVICE EVIDENCE

Before-service photo must capture:

- Photo
- Timestamp
- Latitude
- Longitude
- Booking ID
- Captain ID
- Vehicle registration
- Evidence type = BEFORE

After-service photo:

- Photo
- Timestamp
- Latitude
- Longitude
- Booking ID
- Captain ID
- Vehicle registration
- Evidence type = AFTER

Important:

Browser camera APIs do not guarantee GPS metadata embedded inside the image itself.

Therefore store location metadata alongside the image record.

This is the authoritative service evidence.

---

# 34. VEHICLE REGISTRATION

Captain must enter the vehicle registration number.

Normalize it before storing.

Example:

"MP 09 AB 1234"

should have normalized representation:

"MP09AB1234"

Use normalized registration for duplicate/offer eligibility checks.

Store original display value separately if necessary.

---

# 35. REGISTRATION VALIDATION

Registration number should:

- Reject clearly invalid formats
- Normalize spaces
- Normalize case
- Prevent impossible empty values

Do not aggressively reject legitimate regional formats without configurable validation.

Admin should be able to configure validation strategy if required.

---

# 36. SERVICE COMPLETION

Captain cannot mark completed until required evidence is present.

Required:

- Before photo
- Vehicle registration
- Service started
- After photo
- Completion timestamp
- Completion location
- Required checklist completed

Only then:

SERVICE COMPLETED

---

# 37. CUSTOMER REVIEW

After completion:

Customer sees:

"How was your service?"

Rating:

1–5 stars

Optional:

Comment

Review becomes linked to:

- Booking
- Customer
- Captain
- Service
- Service center

Managers/admin can analyze ratings.

---

# 38. SUBSCRIPTION SYSTEM

Subscriptions are not generic plans.

Every subscription must define:

- Plan name
- Billing period
- Vehicle types
- Included service
- Number of services
- Price
- Validity
- Start date
- End date
- Usage rules
- Eligibility
- Active/inactive
- Terms

Example:

Monthly Basic

Vehicle:

Sedan

Services:

4 Normal Wash

Price:

₹X

Validity:

30 days

---

# 39. ADMIN SUBSCRIPTION MANAGEMENT

Admin must be able to:

- Create plan
- Edit plan
- Disable plan
- Archive plan
- Change price
- Change duration
- Change service count
- Change eligible vehicle types
- Change included service
- Change usage rules
- Set display order
- Set promotional pricing
- Set active period

Do not hardcode subscription plans.

---

# 40. SUBSCRIPTION PURCHASE

When customer purchases a plan, create a subscription record.

Store:

- User ID
- Vehicle ID
- Vehicle type
- Plan ID
- Plan snapshot
- Price paid
- Purchase date
- Start date
- Expiry date
- Total services
- Used services
- Remaining services
- Status

Plan snapshot is important.

If admin later changes the plan, existing subscriptions must retain their purchased terms.

---

# 41. SUBSCRIPTION USAGE

Customer dashboard should show:

SUBSCRIPTION

4 services included

2 used

2 remaining

Expires:

Date

Also show:

- Service history
- Next eligible date
- Included services
- Vehicle
- Validity

---

# 42. SUBSCRIPTION SERVICE CLAIM RULE

If a subscription allows one service per day:

Customer cannot claim multiple subscription services on the same vehicle on the same day.

After claiming today's service:

Next eligible date = next calendar day.

Do not allow continuous same-day claims.

This rule must be configurable per plan.

---

# 43. SUBSCRIPTION EXPIRATION

If services remain unused when subscription expires:

Those unused services expire unless the plan explicitly permits rollover.

Default:

NO ROLLOVER.

Expired services cannot be booked.

Admin must be able to configure rollover behavior for future plans.

---

# 44. SUBSCRIPTION BOOKING VALIDATION

Before allowing a subscription booking:

Check:

- Subscription exists
- Subscription active
- Not expired
- Vehicle matches
- Service included
- Remaining services > 0
- Today's usage rule
- Booking date valid
- Booking time valid
- Service center available

If any condition fails:

Reject booking with a clear customer-friendly explanation.

---

# 45. OFFERS AND PROMOTIONS

Offers must be fully admin-controlled.

Admin can configure:

- Offer name
- Discount type
- Percentage discount
- Fixed discount
- Maximum discount
- Start date
- End date
- Eligible customers
- Eligible services
- Eligible vehicle types
- Eligible centers
- Minimum order
- Usage limit
- Per-user limit
- Active/inactive
- First-service-only
- New-user-only
- Coupon code if applicable

---

# 46. FIRST-WASH OFFER FRAUD PREVENTION

First-service offers must NOT rely only on user account ID.

Eligibility must consider both:

CUSTOMER MOBILE NUMBER

AND

VEHICLE REGISTRATION NUMBER

Example:

Person uses mobile:

9999999999

Car:

MP09AB1234

They use first-wash offer.

Later they create another account:

8888888888

but submit:

MP09AB1234

The first-wash offer must NOT be available.

Likewise, depending on business rules, suspicious reuse of mobile numbers across vehicles can be flagged.

Create an OfferEligibilityService.

Never allow frontend-only offer validation.

---

# 47. CUSTOMER ACCOUNT CREATION BY MANAGER

Managers can create bookings for customers who call the business.

Flow:

Manager enters:

- Name
- Mobile
- Vehicle
- Vehicle type
- Address
- Service
- Date
- Time
- Payment mode

If customer does not already exist:

Create customer account.

Generate temporary password/credential according to configured authentication policy.

Booking is created.

Customer receives account/booking information through notification abstraction.

In Phase 1:

Create notification record/log and provider interface.

SMS/WhatsApp integration will be connected later.

---

# 48. FIRST LOGIN

If account was created by manager:

Set:

mustChangePassword = true

On first login:

Customer can:

- Change password
- Continue to profile

Do not allow the temporary password to remain permanently.

---

# 49. CUSTOMER PROFILE

Profile includes:

- Name
- Mobile
- Email
- Password
- Vehicles
- Addresses
- Booking history
- Subscription
- Reviews
- Offers
- Support
- Notification preferences

---

# 50. VEHICLE MANAGEMENT

Customer can add multiple vehicles.

Vehicle fields:

- Vehicle type
- Registration number
- Brand
- Model
- Color
- Nickname optional

Registration number must be normalized.

A customer should be able to book directly from a saved vehicle.

---

# 51. CUSTOMER BOOKING HISTORY

Show:

- Upcoming
- Active
- Completed
- Cancelled
- Rescheduled

Each booking should display:

- Service
- Vehicle
- Date
- Time
- Captain
- Service center
- Price
- Status
- Evidence where appropriate
- Invoice
- Review

---

# 52. CANCELLATION

Cancellation rules must be configurable.

Example:

Customer may cancel until X minutes before service.

After cutoff:

- cancellation may be restricted
- cancellation fee may apply
- manager override may be required

Never hardcode cancellation behavior.

Store:

- Cancellation reason
- Cancelled by
- Timestamp
- Fee
- Refund information if applicable

---

# 53. RESCHEDULING

Customer or manager may reschedule according to permissions.

New date/time must pass all validations again:

- Future time
- Minimum lead time
- Operating hours
- Service duration
- Service-center availability
- Captain availability if already assigned

Old assignment must be released.

New assignment must be created.

Keep complete history.

---

# 54. REASSIGNMENT

Manager can reassign a booking if:

- Captain unavailable
- Captain cancels
- Emergency
- Customer request
- Operational issue

Old captain assignment becomes:

REASSIGNED

New captain receives assignment.

Keep both records.

Never overwrite history.

---

# 55. CAPTAIN UNAVAILABLE

If captain becomes unavailable:

Manager dashboard must identify affected bookings.

Show:

"Reassignment Required"

Manager can choose:

- Another captain
- Reschedule
- Cancel

Do not silently lose the booking.

---

# 56. CAPTAIN ATTENDANCE

Manager dashboard needs separate:

ATTENDANCE & FIELD LOCATION

screen.

Display:

- Captain name
- Status
- Check-in time
- Check-in coordinates
- Check-out time
- Check-out coordinates
- Current operational state
- Assigned jobs
- Attendance history

Use map-ready architecture.

---

# 57. CAPTAIN LOCATION PRIVACY

Do not continuously track captain location in Phase 1 unless required.

Capture location at meaningful operational events:

- Check-in
- Check-out
- Travel started
- Arrival
- Before photo
- After photo

Future Phase 2 may introduce real-time tracking through WebSockets.

---

# 58. MANAGER SERVICE CREATION

Managers may be allowed to create local bookings for customers.

Do not automatically give managers permission to globally change services/prices.

Global configuration belongs to Admin.

Use permissions:

BOOKING_CREATE

BOOKING_ASSIGN

CUSTOMER_CREATE

etc.

---

# 59. INVENTORY

Service centers may maintain inventory.

Initial inventory:

- Shampoo
- Foam
- Microfiber
- Polish
- Cleaning chemicals
- Gloves
- Other consumables

Admin configures inventory types.

Manager sees:

- Current stock
- Low stock
- Usage
- Restock
- Adjustments

Every stock adjustment must record:

- Who
- When
- Quantity
- Reason

---

# 60. CRM

CRM must provide a 360-degree customer view.

Customer profile should show:

CONTACT

VEHICLES

BOOKINGS

SUBSCRIPTIONS

SPEND

LAST SERVICE

PREFERRED SERVICES

COMPLAINTS

REVIEWS

OFFERS USED

NOTES

SERVICE CENTER

CAPTAIN HISTORY

---

# 61. CUSTOMER DUPLICATE DETECTION

Before creating customer:

Search by normalized mobile.

If existing customer:

Do not create duplicate account automatically.

Use existing account.

Also detect:

- Duplicate vehicle registration
- Duplicate phone
- Suspicious combinations

Admin should be able to merge duplicate customer records through a controlled workflow.

Never silently merge records.

---

# 62. COMPLAINT MANAGEMENT

Customer can create complaint against completed service.

Fields:

- Booking
- Category
- Description
- Photos if required
- Priority

Statuses:

- Open
- Under Review
- Assigned
- Resolved
- Rejected
- Escalated

Manager handles center-level complaints.

Admin can override/escalate.

---

# 63. ADMIN DASHBOARD

Admin homepage should show operational KPIs.

Examples:

Today's bookings

Today's revenue

Pending assignments

Active services

Completed services

Cancelled

Reassignments

Active customers

Subscriptions

Service centers

Captains working

Customer rating

---

# 64. ADMIN SERVICE CENTER MANAGEMENT

Admin can:

Create

Edit

Activate

Deactivate

Archive

View

Configure

Each service center.

Fields:

- Name
- Code
- Address
- Latitude
- Longitude
- Radius
- Priority
- Opening time
- Closing time
- Working days
- Manager
- Status
- Capacity
- Contact information

---

# 65. ADMIN CAPTAIN MANAGEMENT

Admin can:

- Create captain
- Edit profile
- Activate/deactivate
- Assign service center
- View attendance
- View bookings
- View performance
- View ratings
- View service history
- View location events
- Reset password
- Suspend account

---

# 66. ADMIN MANAGER MANAGEMENT

Admin can:

- Create manager
- Assign center
- Change center
- Activate/deactivate
- Set permissions
- View activity
- Reset password
- Audit actions

---

# 67. RBAC

Do not simply use:

if role == admin

throughout the application.

Build a permission system.

Example permissions:

USER_VIEW

USER_EDIT

USER_DELETE

SERVICE_VIEW

SERVICE_CREATE

SERVICE_EDIT

SERVICE_DELETE

PRICE_EDIT

PLAN_CREATE

PLAN_EDIT

BOOKING_VIEW

BOOKING_CREATE

BOOKING_ASSIGN

BOOKING_REASSIGN

BOOKING_CANCEL

CAPTAIN_VIEW

CAPTAIN_ASSIGN

CENTER_CREATE

CENTER_EDIT

CRM_VIEW

REPORT_VIEW

SETTINGS_EDIT

AUDIT_VIEW

etc.

Admin can manage permissions.

---

# 68. ADMIN CONFIGURATION CENTER

Create a central settings module.

Admin should be able to configure:

- Operating hours
- Minimum booking lead time
- Captain opening window
- Cancellation cutoff
- Rescheduling rules
- Subscription usage rules
- Offer rules
- Customer limits
- Service center allocation
- Booking priority
- Notification preferences
- Image requirements
- Attendance settings

Avoid hardcoded business rules.

---

# 69. BOOKING CONFIGURATION

Admin should control:

- Minimum booking advance
- Maximum future booking window
- Service buffer
- Captain early-access time
- Cancellation window
- Reschedule window
- Maximum daily bookings
- Center capacity

---

# 70. SERVICE CENTER CAPACITY

Each center may have capacity.

Capacity can be represented as:

- Maximum concurrent bookings
- Maximum daily bookings
- Maximum active captains

The system must not accept unlimited bookings if capacity is exceeded.

If capacity is exceeded:

- Show unavailable
- Suggest another time
- Suggest another service date

Do not route customers to another center unless the business rules explicitly allow it.

---

# 71. BOOKING AVAILABILITY ENGINE

Build a dedicated AvailabilityService.

Inputs:

- Service
- Vehicle type
- Date
- Start time
- Service center
- Captains
- Operating hours
- Existing bookings
- Capacity
- Rules

Output:

AVAILABLE

or

UNAVAILABLE

with reason.

Possible reasons:

- Outside operating hours
- Less than 30 minutes lead time
- Service does not fit before closing
- No captain available
- Center capacity full
- Holiday
- Service unavailable

---

# 72. SERVER-SIDE VALIDATION

Every important rule must exist on backend.

Frontend validation is for UX.

Backend validation is authoritative.

Never trust:

- Client price
- Client discount
- Client role
- Client service duration
- Client service-center ID
- Client booking status
- Client location
- Client subscription count

Server must recalculate/verify.

---

# 73. PRICE SECURITY

Never accept final price blindly from frontend.

Frontend may send:

serviceId

vehicleTypeId

coupon

subscription

Backend calculates:

base price

discount

tax if applicable

final price

Then stores snapshot.

---

# 74. BOOKING PRICE SNAPSHOT

Booking should store:

Service name snapshot

Vehicle type snapshot

Base price

Discount

Coupon

Final price

Duration

Payment mode

Tax if applicable

This protects historical accuracy.

---

# 75. SUBSCRIPTION PRICE SNAPSHOT

Subscription purchase stores the plan details at purchase time.

Admin changes later must not alter the customer's existing purchased subscription.

---

# 76. DATABASE DESIGN

Use MongoDB.

Core collections:

users

roles

permissions

vehicles

addresses

service_centers

services

vehicle_types

service_prices

subscription_plans

subscriptions

subscription_usage

offers

offer_redemptions

bookings

booking_assignments

booking_status_history

booking_evidence

captain_attendance

inventory_items

inventory_transactions

reviews

complaints

notifications

invoices

audit_logs

system_settings

content_pages

faqs

testimonials

media

support_tickets

---

# 77. DATABASE INDEXING

Plan indexes from the beginning.

Important examples:

Users:

mobile normalized unique

email unique where applicable

Vehicles:

registration normalized index

Bookings:

serviceCenterId + startDateTime

captainId + startDateTime

customerId + createdAt

status + startDateTime

Subscriptions:

customerId + status

Offers:

mobile/vehicle eligibility references

Attendance:

captainId + date

Do not allow unbounded collection scans for common operational queries.

---

# 78. DATABASE CONCURRENCY

Critical operations must be atomic.

Examples:

Captain assignment

Subscription usage

Offer redemption

Inventory deduction

Booking status transition

Do not assume two requests cannot happen simultaneously.

Use atomic operations and MongoDB transactions where multiple documents must change together.

---

# 79. IDEMPOTENCY

Important actions should support idempotency.

Examples:

Create booking

Confirm payment

Assign captain

Complete service

Redeem offer

If the same request is submitted twice, system must not create duplicate business records.

---

# 80. AUDIT LOG

Admin must be able to see important changes.

Audit record:

- Actor
- Role
- Action
- Entity
- Entity ID
- Old value
- New value
- Timestamp
- IP if appropriate
- Device/session metadata if appropriate

Examples:

Admin changed service price.

Manager reassigned booking.

Captain completed booking.

Admin changed service-center radius.

Manager changed customer details.

---

# 81. SOFT DELETE

Do not physically delete important operational records.

Use:

- active
- archived
- deletedAt

Historical bookings, payments, evidence, subscriptions, and audit records must remain traceable.

---

# 82. ADMIN ANALYTICS

Analytics must be useful, not decorative.

Include:

Revenue

Bookings

Completed rate

Cancellation rate

Repeat customer rate

Subscription conversion

Subscription retention

Average order value

Average service duration

Captain utilization

Service center performance

Top services

Top vehicle categories

Offer usage

Customer rating

Complaint rate

---

# 83. SERVICE CENTER ANALYTICS

For each center:

- Today's bookings
- Completed
- Pending
- Cancelled
- Revenue
- Average rating
- Captain utilization
- Service demand
- Peak hours
- Customer count
- Subscription usage
- Complaint rate

---

# 84. CAPTAIN PERFORMANCE

Metrics:

- Jobs completed
- Average completion time
- Rating
- Cancellation/failed jobs
- Attendance
- On-time performance
- Customer complaints
- Service-center assignment

Do not use metrics to unfairly punish captains automatically.

Display data transparently.

---

# 85. NOTIFICATIONS ARCHITECTURE

Phase 1 should create a NotificationService abstraction.

Notification channels:

- SMS
- WhatsApp
- Email
- Push

Providers will be connected later.

Create notification records now.

Examples:

BOOKING_CREATED

BOOKING_ASSIGNED

BOOKING_RESCHEDULED

BOOKING_CANCELLED

CAPTAIN_ASSIGNED

SERVICE_COMPLETED

SUBSCRIPTION_PURCHASED

SUBSCRIPTION_EXPIRING

PASSWORD_CREATED

---

# 86. WHATSAPP/SMS PHASE 2 PREPARATION

Do not directly place WhatsApp API calls throughout booking code.

Use:

NotificationService

Later:

WhatsAppProvider

SMSProvider

EmailProvider

This prevents architectural rewrites.

---

# 87. PAYMENT ARCHITECTURE

Support payment modes:

- Cash
- Online

Phase 1 may use:

- Cash
- Mock/demo online payment state

Razorpay integration will be added later.

Payment records should support:

- pending
- initiated
- successful
- failed
- refunded
- cash_pending
- cash_collected

Do not mark online payment successful merely because frontend says so.

---

# 88. INVOICE

Booking invoice should show:

- Invoice number
- Customer
- Vehicle
- Service
- Date
- Service center
- Base amount
- Discount
- Tax if applicable
- Final amount
- Payment status
- Payment mode

Invoice values are immutable historical snapshots.

---

# 89. CUSTOMER COMMUNICATION

Booking confirmation should eventually send:

- Booking ID
- Service
- Vehicle
- Date
- Time
- Address
- Price
- Service center
- Login/account information where applicable

Phase 1 stores notification events.

Phase 2 connects actual WhatsApp/SMS.

---

# 90. FRONTEND ARCHITECTURE

Use:

React

Vite

TypeScript

Tailwind CSS

React Router

TanStack Query

React Hook Form

Zod

Axios

Lucide icons

Use a proper component system.

Suggested structure:

src/

app/

components/

layouts/

pages/

features/

hooks/

services/

api/

schemas/

types/

utils/

constants/

store/

assets/

---

# 91. FEATURE-BASED FRONTEND ORGANIZATION

Organize large modules by feature.

Example:

features/

auth/

booking/

customer/

captain/

manager/

admin/

services/

subscriptions/

crm/

analytics/

inventory/

reviews/

notifications/

This is preferable to placing every component into one giant components folder.

---

# 92. BACKEND ARCHITECTURE

FastAPI.

Suggested structure:

app/

api/

routers/

schemas/

models/

services/

repositories/

domain/

core/

config/

database/

middleware/

security/

storage/

notifications/

location/

analytics/

audit/

utils/

tests/

Business logic belongs in services/domain.

Database access belongs in repositories.

Routes should remain thin.

---

# 93. API VERSIONING

Use:

/api/v1/

Do not expose random unversioned endpoints.

---

# 94. API RESPONSE FORMAT

Use consistent responses.

Example conceptual format:

success

data

message

meta

For errors:

errorCode

message

details

requestId

Never expose internal stack traces to users.

---

# 95. ERROR HANDLING

Customer error:

"Please select a time at least 30 minutes from now."

Not:

ValueError: invalid datetime.

Manager error:

"Captain is no longer available for this booking."

Admin error:

"Service cannot be disabled because active subscriptions currently depend on it."

Errors must be meaningful.

---

# 96. SERVICE DEACTIVATION

Admin cannot blindly delete a service that is currently referenced by active subscriptions or future bookings.

Provide:

- Disable for new bookings
- Preserve existing bookings
- Preserve historical records

Similarly, vehicle types and subscription plans require dependency checks.

---

# 97. ADMIN CONTENT MANAGEMENT

Landing page should be manageable.

Admin can edit:

- Hero title
- Hero description
- Hero image
- Services display
- Offers
- FAQ
- Testimonials
- Contact information
- Footer
- About content

However, do not make the CMS unnecessarily complicated.

Provide structured forms.

---

# 98. MEDIA MANAGEMENT

Cloudinary-ready.

Media record should include:

- URL
- Public ID
- Type
- Entity
- Uploaded by
- Timestamp

Admin can view and replace media.

Do not scatter raw Cloudinary URLs throughout database business records without abstraction.

---

# 99. ACCESSIBILITY

Customer-facing UI must be accessible.

Use:

- Semantic HTML
- Labels
- Keyboard navigation
- Focus states
- Accessible dialogs
- Accessible buttons
- Proper contrast
- Error messages associated with fields
- Screen-reader labels

Do not use color as the only status indicator.

---

# 100. RESPONSIVE DESIGN

Must work on:

- Mobile
- Tablet
- Laptop
- Desktop
- Large monitors

Customer booking must be excellent on mobile.

Manager/admin dashboards must also remain usable on tablets.

Do not create desktop-only tables that become unusable on mobile.

Use responsive tables/cards.

---

# 101. ADMIN UI PRINCIPLE

Admin dashboard can be information-dense.

Customer dashboard must be simple.

Captain dashboard must be action-oriented.

Manager dashboard must be operational.

Admin dashboard must be analytical/configurable.

Do not use the same UI philosophy for every role.

---

# 102. CAPTAIN MOBILE EXPERIENCE

Captain interface should be designed primarily for mobile.

Large action buttons.

Clear status.

Minimal typing.

Camera access should be easy.

Location permission should be clearly explained.

Do not overload captain with unnecessary analytics.

Their primary objective is:

KNOW THE JOB → REACH CUSTOMER → PERFORM SERVICE → CAPTURE PROOF → COMPLETE.

---

# 103. MANAGER UI PRINCIPLE

Manager should be able to operate a service center from one dashboard.

Important information should appear without opening multiple pages.

Primary actions:

- Assign
- Reassign
- Reschedule
- View captain
- View booking
- Contact customer
- Resolve issue

---

# 104. ADMIN UI PRINCIPLE

Admin needs full control but should not be overwhelmed.

Use:

Sidebar

Dashboard

Operations

Customers

Bookings

Service Centers

Services

Subscriptions

Offers

Staff

CRM

Inventory

Reports

Analytics

Content

Settings

Audit Logs

Use search and filters heavily.

---

# 105. GLOBAL SEARCH

Admin should eventually have global search.

Search:

- Customer
- Mobile
- Registration number
- Booking ID
- Captain
- Manager
- Service center
- Subscription

Registration number and mobile should be especially fast.

---

# 106. FILTERING

Operational lists need:

- Search
- Date range
- Status
- Service center
- Service
- Vehicle type
- Captain
- Manager
- Payment mode

Pagination must be server-side.

---

# 107. BOOKING ID

Every booking receives a unique human-friendly booking ID.

Example conceptual:

DC-2026-000001

Do not expose MongoDB ObjectId as the main business identifier.

---

# 108. SERVICE CENTER CODE

Each center gets unique code.

Example:

DC-DEL-01

DC-IND-02

Admin controlled.

---

# 109. CAPTAIN ID

Every captain should have unique employee identifier.

Example:

CAP-000123

---

# 110. CUSTOMER ID

Every customer should have unique internal customer ID.

---

# 111. TIMEZONE

Store timestamps in UTC internally.

Display according to configured business timezone.

Default:

Asia/Kolkata

Do not store ambiguous local date strings as the authoritative timestamp.

---

# 112. DATE/TIME VALIDATION

Never trust browser clock.

Backend uses server time.

Booking availability must be calculated using authoritative backend time.

This prevents customers from manipulating their device time.

---

# 113. SECURITY

Implement:

- Password hashing
- JWT
- Refresh token rotation where appropriate
- RBAC
- Input validation
- CORS configuration
- Secure cookies where applicable
- File validation
- API authorization
- Request limits architecture
- Audit logs
- Secret management
- Environment variables

Never store plaintext passwords.

---

# 114. FILE SECURITY

Before storing image:

Validate:

- MIME type
- File size
- Image dimensions
- Extension
- Upload origin

For camera evidence, use controlled capture flow.

---

# 115. CAMERA EVIDENCE SECURITY

Evidence must be tied to:

- Booking
- Captain
- Customer vehicle
- Timestamp
- Location
- Evidence type

Do not allow captain to select an arbitrary old image.

---

# 116. LOCATION VALIDATION FOR CAPTAIN

When capturing operational evidence:

Check that location exists.

Optionally compare captain coordinates against customer/service location.

Do not automatically reject minor GPS drift because mobile GPS is imperfect.

Create configurable acceptable radius.

Example:

100 meters.

Admin can configure.

---

# 117. SERVICE CENTER CHECK-IN VALIDATION

Captain check-in should verify captain is near assigned service center if that policy is enabled.

For example:

Captain must be within configured center radius.

If outside:

Show warning or prevent check-in depending on admin setting.

---

# 118. BOOKING CUSTOMER LOCATION VALIDATION

When booking is created:

Confirm that customer coordinates belong to a supported service area.

If outside:

Show:

"Sorry, we currently do not serve this location."

Do not create an orphan booking unless manager/admin explicitly creates one.

---

# 119. OUT-OF-SERVICE-AREA BOOKING

Manager may create an override booking only if permission exists.

Record:

- Override reason
- Authorized manager
- Timestamp

Admin can audit these.

---

# 120. SERVICE CENTER ALLOCATION FAILURE

If no center matches:

Do not assign randomly.

Booking should remain:

SERVICE_AREA_UNAVAILABLE

or prevent confirmation.

Admin/manager can review manually if business wants this workflow.

---

# 121. BOOKING ASSIGNMENT FAILURES

If no captain is available:

Do not fake assignment.

Display:

"No captain available for this time."

Options:

- Choose another time
- Manager review
- Waitlist if enabled

Waitlist should be a future feature unless explicitly implemented.

---

# 122. BOOKING WAITLIST ARCHITECTURE

Prepare an extension point for:

WaitlistService

Customer requests unavailable slot.

If captain becomes available:

Notify eligible customer.

Not required for initial implementation.

---

# 123. CRITICAL DATA RULE

Never delete historical data just because configuration changes.

Historical bookings must retain:

- Service snapshot
- Vehicle type snapshot
- Price snapshot
- Duration snapshot
- Customer snapshot where appropriate
- Service center snapshot where necessary

This is essential for accounting and auditability.

---

# 124. ADMIN CHANGE SAFETY

For sensitive configuration:

Price changes

Service duration

Subscription terms

Offer rules

Service-center radius

Opening hours

Require confirmation.

Show:

Current value

New value

Impact warning

Confirm action

---

# 125. CONFIGURATION HISTORY

Sensitive settings should maintain history.

Example:

Service duration:

Old: 30 minutes

New: 40 minutes

Changed by:

Admin X

Changed at:

Timestamp

---

# 126. OPERATIONAL EVENT SYSTEM

Create internal domain events conceptually:

BookingCreated

BookingAssigned

CaptainAccepted

TravelStarted

CaptainArrived

ServiceStarted

ServiceCompleted

BookingCancelled

SubscriptionPurchased

SubscriptionUsed

OfferRedeemed

ReviewCreated

These can later power:

- WebSockets
- Notifications
- Analytics
- Background jobs

without rewriting core logic.

---

# 127. REDIS

Redis is NOT a mandatory dependency for Phase 1.

Do not make the entire system fail because Redis is unavailable.

Create an abstraction for:

- Cache
- Distributed locks
- Rate limiting
- Temporary OTP
- Queue support

Phase 1 may use database/in-memory development implementations.

Redis can be introduced later.

---

# 128. WEBSOCKETS

Do not make WebSockets mandatory in Phase 1.

However, architecture should allow:

- New booking notification
- Captain assignment notification
- Booking status updates
- Live captain location
- Manager dashboard realtime updates

Later.

---

# 129. GOOGLE MAPS

Prepare:

LocationProvider interface.

Later integrate:

- Address autocomplete
- Reverse geocoding
- Distance
- Directions
- Service-center visualization
- Customer location
- Captain location
- Zone visualization

Do not tightly couple the UI to Google-specific data structures.

---

# 130. RAZORPAY

Prepare:

PaymentProvider interface.

Later support:

- Payment order creation
- Checkout
- Verification
- Webhook
- Refund
- Payment status reconciliation

Never trust frontend payment success.

---

# 131. WHATSAPP

Prepare notification templates.

Examples:

BOOKING_CONFIRMATION

CAPTAIN_ASSIGNED

BOOKING_RESCHEDULED

SERVICE_COMPLETED

SUBSCRIPTION_PURCHASED

SUBSCRIPTION_EXPIRING

CUSTOMER_CREATED

---

# 132. ADMIN NOTIFICATION CENTER

Admin should be able to see important operational alerts.

Examples:

- Unassigned booking
- Imminent booking
- Captain absent
- Booking failed
- Service-center capacity reached
- Complaint escalated
- Low inventory
- System error

---

# 133. MANAGER ALERTS

Manager sees:

- New booking
- Booking approaching
- Unassigned booking
- Captain unavailable
- Customer cancellation
- Reschedule
- Complaint
- Low stock

---

# 134. CUSTOMER NOTIFICATIONS

Customer eventually receives:

- Booking confirmation
- Captain assignment
- Reminder
- Service started
- Completed
- Review request
- Subscription information

---

# 135. REPORTING

Admin reports:

- Revenue
- Booking volume
- Service performance
- Subscription performance
- Customer growth
- Center performance
- Captain performance
- Offer performance
- Cancellation
- Complaints

Allow:

- Date filters
- Center filters
- Export-ready architecture

---

# 136. DATA EXPORT

Admin should eventually be able to export:

- Customers
- Bookings
- Revenue
- Subscriptions
- Captains
- Inventory
- Complaints

Exports must respect permissions.

---

# 137. CUSTOMER DELETION

If customer requests account deletion:

Do not destroy legally/business-required transaction history.

Use anonymization/deactivation where appropriate.

Preserve required operational records.

---

# 138. BUSINESS RULE ENGINE PRINCIPLE

Business rules should be centralized.

Avoid this:

Component A has one booking rule.

Component B has another.

API C has another.

Instead:

BookingPolicyService

SubscriptionPolicyService

OfferEligibilityService

AvailabilityService

AssignmentService

LocationService

PricingService

This keeps the platform consistent.

---

# 139. TESTING REQUIREMENTS

Do not consider feature complete simply because the page renders.

Test:

Authentication

RBAC

Booking

Pricing

Availability

Subscription

Offers

Location

Service-center assignment

Captain assignment

Camera evidence

Attendance

Cancellation

Rescheduling

Reassignment

CRM

Admin configuration

---

# 140. CRITICAL TEST CASES

Test:

Customer books 10 minutes from now.

Expected:

Rejected.

Customer books 30 minutes from now.

Expected:

Allowed if capacity exists.

Customer selects service that ends after closing.

Expected:

Rejected.

Captain already has overlapping booking.

Expected:

Cannot assign.

Two managers assign same captain simultaneously.

Expected:

Only one succeeds.

Customer outside all service zones.

Expected:

Rejected/unavailable.

Two centers overlap.

Expected:

Deterministic allocation.

Customer changes phone but uses same vehicle for first-wash offer.

Expected:

Offer rejected.

Subscription expired.

Expected:

Cannot claim.

Subscription has no remaining services.

Expected:

Cannot claim.

Captain tries to start 60 minutes early.

Expected:

Rejected.

Captain tries to upload gallery image.

Expected:

No gallery workflow available.

Captain completes without before photo.

Expected:

Rejected.

Captain completes without after photo.

Expected:

Rejected.

---

# 141. UX VALIDATION

Customer should never see technical errors.

Every validation should tell them:

WHAT happened

WHY

WHAT TO DO NEXT

Example:

"This service takes 40 minutes and our center closes at 8:00 PM. Please choose a start time before 7:20 PM."

Not:

"Invalid slot."

---

# 142. CUSTOMER BOOKING SPEED

The ideal booking should take less than a minute for a returning user.

Saved vehicle.

Saved address.

Saved details.

Customer should be able to:

Choose vehicle

Choose service

Choose time

Confirm.

---

# 143. RETURNING CUSTOMER

When logged in:

Show saved vehicles immediately.

Show:

Book Again

Upcoming Booking

Subscription

Recent Services

Do not force users to repeatedly enter their information.

---

# 144. NEW CUSTOMER

Keep registration lightweight.

Minimum:

Name

Mobile

Password or temporary credential flow

Additional profile fields can be completed later.

Do not make customers fill 15 fields before booking.

---

# 145. CUSTOMER BOOKING SUMMARY

Always clearly show:

SERVICE

VEHICLE

DATE

TIME

DURATION

ADDRESS

PRICE

DISCOUNT

PAYMENT

SERVICE CENTER

Then:

CONFIRM BOOKING

---

# 146. ADMIN SERVICE MANAGEMENT UX

Service table:

Service

Image

Status

Vehicle types

Starting price

Duration

Active bookings

Actions

Edit

Disable

Archive

View

---

# 147. ADMIN PRICING UX

Service detail page should have a clear pricing matrix.

Example:

| Vehicle Type | Price |
| Hatchback | ₹ |
| Sedan | ₹ |
| 5-Seater SUV | ₹ |
| 7-Seater SUV | ₹ |
| Luxury | ₹ |
| Jeep | ₹ |

Admin can edit each.

Do not make pricing hidden in another page.

---

# 148. ADMIN SUBSCRIPTION UX

Plan builder:

Plan name

Billing duration

Vehicle types

Included services

Number of uses

Price

Validity

Usage rule

Rollover

Eligibility

Active period

Status

Preview

Save

---

# 149. OFFER BUILDER

Admin should be able to visually configure:

Offer name

Discount

Eligibility

First service

Vehicle

Service

Date range

Usage limit

Customer limit

Center

Status

Offer preview.

---

# 150. SERVICE CENTER MAP UI

Admin should eventually see:

Service centers on map.

Each center:

- Name
- Radius
- Status
- Manager
- Captains
- Booking count

Draw service radius visually when Maps is available.

Until then provide map-ready component/mock map.

---

# 151. MULTI-CENTER DATA ISOLATION

Manager A must never see:

Manager B's bookings

Manager B's customers unless explicitly authorized

Manager B's captains

Manager B's inventory

Manager B's reports

Manager API queries must automatically scope by serviceCenterId.

Do not rely only on frontend filtering.

Backend authorization must enforce it.

---

# 152. ADMIN GLOBAL ACCESS

Admin can see all centers.

Admin filters:

All Centers

Center A

Center B

Center C

etc.

---

# 153. MANAGER CUSTOMER CREATION

If customer calls:

Manager creates account + booking.

The customer later logs in and sees:

Profile

Booking

Vehicle

Service history

Subscription

Everything linked to the same account.

---

# 154. CUSTOMER MOBILE IDENTITY

Mobile number is a primary business identity.

Normalize:

Country code

Number

Format

Use normalized value for:

- Login
- Duplicate detection
- First-offer validation
- Notifications

---

# 155. OFFER REDEMPTION RECORD

When offer is used store:

Offer ID

Customer ID

Mobile snapshot

Vehicle ID

Registration snapshot

Booking ID

Discount

Timestamp

Eligibility decision

This creates an audit trail.

---

# 156. SUBSCRIPTION USAGE RECORD

Each usage should create:

Subscription ID

Booking ID

Customer ID

Vehicle ID

Service

Usage date

Remaining before

Remaining after

Timestamp

This prevents unreliable counters.

---

# 157. COUNTER CONSISTENCY

Remaining subscription services should not be trusted only from a client-side counter.

Backend calculates/updates usage atomically.

Prevent double booking against the final remaining service.

---

# 158. BOOKING DUPLICATION PREVENTION

Prevent accidental double-click booking.

Use:

- Idempotency key
- Request ID
- Duplicate submission protection

Customer clicking Confirm twice must not create two bookings.

---

# 159. MANAGER BOOKING DUPLICATION

Same protection applies to manager-created bookings.

---

# 160. CAPTAIN ACTION DUPLICATION

Captain clicking:

Complete

twice

must result in only one completion.

---

# 161. TIME CONFLICT DETECTION

All assignment and availability logic must use intervals.

Represent:

startAt

endAt

Not merely:

bookingTime = "4:00 PM"

---

# 162. BUFFER TIME

Architecture should support optional travel/setup buffer.

Example:

Service:

40 min

Buffer:

10 min

Availability interval:

12:00 → 12:50

Admin controls buffer.

Default may be zero if not yet required.

---

# 163. CAPTAIN TRAVEL CONSTRAINT

Future location integration should consider travel distance/time.

Phase 1 may only prevent overlapping jobs.

Phase 2 should calculate whether a captain can realistically travel from Booking A to Booking B.

Do not pretend travel feasibility is solved if Maps is not connected.

---

# 164. CUSTOMER LOCATION SHARING

Customer booking location must be visible to assigned captain.

Display:

- Address
- Coordinates
- Map-ready navigation button

When Google Maps is integrated, navigation should open through proper map routing.

---

# 165. CAPTAIN ADDRESS ACCESS

Captain should not need to manually search customer information.

Booking card should clearly display:

Customer

Phone/contact according to privacy policy

Address

Vehicle

Service

Time

---

# 166. CUSTOMER CONTACT PRIVACY

Expose only information necessary for service fulfillment.

Do not expose unrelated customer information.

---

# 167. MANAGER OPERATIONAL BOARD

Consider a Kanban-style operational board:

NEW

ASSIGNING

ASSIGNED

TRAVELING

ARRIVED

IN SERVICE

COMPLETED

ISSUE

This should be easy to understand.

---

# 168. ADMIN GLOBAL OPERATIONS

Admin should be able to view the entire operational pipeline:

All centers

All bookings

All active captains

All assignments

All issues

All customers

---

# 169. DASHBOARD REAL-TIME ARCHITECTURE

Phase 1 may use polling/query refresh.

Do not require WebSockets.

But structure state updates so WebSockets can replace polling later.

---

# 170. LOGGING

Use structured logs.

Every important request should have:

requestId

timestamp

route

user ID if authenticated

status

duration

Errors should include correlation IDs.

---

# 171. OBSERVABILITY PREPARATION

Architecture should allow future:

- Error monitoring
- Metrics
- Performance tracing
- Background job monitoring

Do not build an unnecessarily complicated observability platform in Phase 1.

---

# 172. ENVIRONMENT CONFIGURATION

Separate:

Development

Testing

Production

Use environment variables for:

Database

JWT secret

Cloudinary

Maps

Razorpay

WhatsApp

SMS

Redis

Do not commit secrets.

Provide:

.env.example

---

# 173. DATABASE MIGRATION/SEEDING

Provide seed scripts for development.

Seed:

- Admin
- Manager
- Captain
- Customer
- Vehicle types
- Services
- Pricing
- Subscription plans
- Offers
- Service centers

Seed data must be clearly marked development/demo data.

---

# 174. NO FAKE PRODUCTION DATA

Do not scatter fake arrays throughout components.

If demo data is required:

Create a clear seed/mock data layer.

Production application must fetch from API.

---

# 175. API DOCUMENTATION

FastAPI Swagger/OpenAPI should remain enabled in development.

Document:

- Authentication
- Request models
- Response models
- Errors
- Permissions

---

# 176. FRONTEND API STATE

Use TanStack Query for:

- Bookings
- Services
- Customers
- Captains
- Centers
- Subscriptions
- Analytics

Do not manually duplicate server state unnecessarily.

---

# 177. FORM VALIDATION

Use:

React Hook Form

+

Zod

Frontend schemas should correspond to backend Pydantic schemas.

But backend remains authoritative.

---

# 178. LOADING STATES

Every API operation needs:

- Loading
- Success
- Error
- Empty state

Do not leave blank white screens.

---

# 179. EMPTY STATES

Examples:

No bookings:

"You don't have any bookings yet."

CTA:

Book your first service.

Manager:

"No unassigned bookings."

Admin:

"No service centers found."

Make empty states useful.

---

# 180. CONFIRMATION DIALOGS

Sensitive actions:

Disable service

Cancel booking

Reassign captain

Deactivate user

Change price

Delete/archive plan

Require confirmation.

Show impact.

---

# 181. MOBILE BOOKING UX

Mobile booking should use:

Sticky bottom CTA

Large controls

Simple progress

Readable price

Minimal typing

Location permission flow

Camera/location interfaces designed for one-handed use where possible.

---

# 182. DESIGN CONSISTENCY

Create design tokens:

Colors

Typography

Spacing

Border radius

Shadows

Buttons

Inputs

Cards

Badges

Tables

Modals

Alerts

Do not create slightly different buttons on every page.

---

# 183. ANIMATION

Use subtle animations only.

Allowed:

- Page transitions
- Button feedback
- Modal entrance
- Status changes
- Card hover

Avoid:

- Excessive parallax
- Constant floating animations
- Distracting motion

The service should feel trustworthy, not like an animation showcase.

---

# 184. PERFORMANCE

Optimize:

- Images
- Lazy loading
- API pagination
- Component rendering
- Database queries
- Bundle size

Landing page should load quickly.

Do not load admin dashboard code for customers unnecessarily.

Use route-level code splitting where useful.

---

# 185. SEO

Public pages:

Home

Services

Plans

Offers

About

Contact

FAQ

Use:

- Proper titles
- Meta descriptions
- Semantic headings
- OpenGraph
- Canonical URL architecture
- Structured data where appropriate

Admin/customer applications do not need public SEO.

---

# 186. ACCESS CONTROL FRONTEND

Frontend should hide unauthorized features.

But remember:

Frontend hiding is NOT security.

Backend must enforce every permission.

---

# 187. ADMIN SESSION SECURITY

Admin sessions should be more strictly protected.

Architecture should allow:

- Session expiration
- Refresh
- Device/session management
- Forced logout
- Password reset
- Account suspension

---

# 188. PASSWORD POLICY

Use configurable password requirements.

Never expose password plaintext.

Temporary passwords must require change.

---

# 189. ACCOUNT STATUS

Users can be:

ACTIVE

INACTIVE

SUSPENDED

PENDING

ARCHIVED

Each status has clear login/access behavior.

---

# 190. AUDITABLE ADMIN CONFIGURATION

Every modification to:

Service

Price

Plan

Offer

Center

Radius

Operating hours

Permissions

must create audit history.

---

# 191. BUSINESS DASHBOARD TERMINOLOGY

Use understandable language.

Customer:

"Book a Service"

Captain:

"Today's Jobs"

Manager:

"Bookings to Assign"

Admin:

"Operations"

Avoid overly technical terminology in UI.

---

# 192. ADMIN SERVICE STATUS

Services can be:

DRAFT

ACTIVE

PAUSED

ARCHIVED

DRAFT services are not publicly bookable.

PAUSED services are temporarily unavailable.

ARCHIVED services preserve history.

---

# 193. SUBSCRIPTION PLAN STATUS

Plans:

DRAFT

ACTIVE

PAUSED

EXPIRED

ARCHIVED

Existing subscriptions remain valid according to purchased snapshot.

---

# 194. SERVICE CENTER STATUS

CENTER:

ACTIVE

TEMPORARILY CLOSED

MAINTENANCE

INACTIVE

A closed center must not receive new bookings.

Existing bookings require operational handling.

---

# 195. CAPTAIN STATUS

CAPTAIN:

ACTIVE

ON_LEAVE

OFFLINE

SUSPENDED

INACTIVE

Availability engine must consider status.

---

# 196. MANAGER STATUS

Manager:

ACTIVE

INACTIVE

SUSPENDED

---

# 197. CUSTOMER SERVICE AREA EXPERIENCE

If location unavailable:

"DoorstepCare isn't available at this location yet."

Offer:

"Try another location"

Do not show technical allocation errors.

---

# 198. BOOKING CONFIRMATION PAGE

After successful booking:

Large confirmation.

Show:

Booking ID

Service

Vehicle

Date

Time

Address

Service center

Amount

Payment

Status

CTA:

View Booking

CTA:

Back to Home

---

# 199. CUSTOMER TRUST

Landing page should communicate:

Verified captains

Transparent pricing

Doorstep service

Professional process

Before/after proof

Customer reviews

Easy support

Do not claim certifications or guarantees unless actually provided.

---

# 200. DO NOT OVERBUILD THE CUSTOMER UI

The customer's objective is not to manage the company.

Keep customer navigation:

Home

Bookings

Vehicles

Subscriptions

Profile

Support

Book Now

That's enough.

---

# 201. DO NOT OVERBUILD CAPTAIN UI

Captain navigation:

Today

Jobs

Attendance

History

Profile

Keep it operational.

---

# 202. MANAGER NAVIGATION

Manager:

Dashboard

Bookings

Captains

Attendance

Customers

Inventory

Complaints

Reports

Settings

---

# 203. ADMIN NAVIGATION

Admin:

Dashboard

Operations

Bookings

Customers

CRM

Service Centers

Services

Pricing

Subscriptions

Offers

Captains

Managers

Inventory

Reports

Analytics

Content

Notifications

Audit Logs

Settings

---

# 204. IMPORTANT: NO HARDCODED BUSINESS LOGIC

Never hardcode:

₹ prices

Service duration

Operating hours

Service radius

Subscription limits

Offer percentages

Cancellation window

Lead time

Captain early-access time

Center capacity

Vehicle categories

Service names

These belong in configuration/database.

---

# 205. IMPORTANT: NO HARDCODED UI CONTENT WHERE ADMIN SHOULD CONTROL IT

If Admin is supposed to edit:

- Service name
- Price
- Description
- Image
- Offer
- Plan

then customer UI must load it from backend.

Do not duplicate it into React constants.

---

# 206. DATA CONSISTENCY

If service price changes:

Customer UI updates.

Admin sees updated price.

New bookings use new price.

Old bookings retain old snapshot.

This principle applies to all mutable business configuration.

---

# 207. SERVICE IMAGE REQUIREMENT

Every active public service must have a valid image.

Admin should not be able to activate a public service without required content.

---

# 208. OFFER VALIDATION AT CHECKOUT

At booking confirmation:

Re-evaluate offer.

Never trust an offer previously calculated on service selection screen.

Check:

Current time

Offer validity

Customer

Mobile

Vehicle

Registration

Service

Center

Usage limit

Plan/subscription state

---

# 209. BOOKING CONFIRMATION TRANSACTION

Booking creation should be treated as a business transaction.

Validate:

Customer

Vehicle

Service

Price

Location

Center

Time

Capacity

Offer

Subscription

Payment

Then create booking.

If a critical operation fails:

Do not leave half-created booking data.

Use transaction/compensating logic.

---

# 210. SERVICE COMPLETION TRANSACTION

Completion should validate:

Captain assignment

Current booking state

Evidence

Location

Timing

Then atomically:

Complete booking

Record evidence

Consume subscription usage if applicable

Create notification event

Create audit event

Do not partially complete.

---

# 211. SUBSCRIPTION BOOKING ATOMICITY

If last remaining subscription service is being claimed:

Two simultaneous booking requests must not both consume the same final service.

Use atomic/transactional protection.

---

# 212. OFFER ATOMICITY

If offer has usage limit:

Two simultaneous requests must not exceed the limit.

Use atomic redemption logic.

---

# 213. INVENTORY ATOMICITY

Future inventory consumption must not create negative stock unless admin explicitly allows it.

---

# 214. CUSTOMER REVIEW TIMING

Review should only be possible after:

SERVICE_COMPLETED.

One review per booking unless admin allows modification.

---

# 215. BOOKING EVIDENCE RETENTION

Evidence must remain associated with historical booking.

Do not replace before/after photos when another booking occurs.

---

# 216. LOCATION EVIDENCE

Store:

latitude

longitude

accuracy if available

timestamp

event type

Example:

BEFORE_PHOTO

AFTER_PHOTO

CHECK_IN

CHECK_OUT

ARRIVAL

TRAVEL_STARTED

---

# 217. GPS ACCURACY

If browser provides accuracy:

Store it.

Do not reject every location with poor accuracy automatically.

Admin should configure minimum acceptable accuracy if needed.

---

# 218. OFFLINE/NETWORK FAILURE

Captains may have poor connectivity.

Architecture should prepare for retry.

Critical actions must be safely retryable.

Example:

Captain clicks complete.

Network fails.

Captain retries.

System should not create duplicate completion.

---

# 219. CUSTOMER NETWORK FAILURE

Customer clicks Book Now.

Request times out.

They retry.

System should use idempotency to prevent duplicate booking.

---

# 220. ADMIN OPERATIONS SAFETY

Admin is powerful.

Dangerous operations should have explicit confirmation and permission checks.

Never expose destructive actions without safeguards.

---

# 221. NO DIRECT DATABASE MODIFICATION FROM UI

All changes must go through validated APIs.

---

# 222. NO BUSINESS LOGIC IN COMPONENTS

React components should not calculate complex booking allocation, pricing, subscription eligibility, or captain assignment.

Those belong to backend domain services.

---

# 223. NO DATABASE LOGIC IN ROUTERS

FastAPI route:

authenticate

validate

call service

return response.

Do not place hundreds of lines of MongoDB queries in routers.

---

# 224. PRODUCTION DATABASE DESIGN

Use appropriate MongoDB indexes.

Avoid excessive population/joins.

Denormalize carefully for operational reads.

Snapshot historical business values.

Keep transactional data authoritative.

---

# 225. SCALABILITY PRINCIPLE

Design stateless backend instances.

Do not store critical state only in server memory.

This allows future horizontal scaling.

---

# 226. BACKGROUND JOB PREPARATION

Future background tasks:

- Notifications
- Subscription expiration
- Reminder messages
- Reports
- Image processing
- Analytics
- Cleanup

Create service abstractions but do not introduce unnecessary infrastructure in Phase 1.

---

# 227. ADMIN SYSTEM HEALTH

Prepare an admin-only system health area showing:

Database connectivity

Storage status

Notification provider status

Map provider status

Payment provider status

Later integrations can populate this.

---

# 228. FEATURE FLAGS

Prepare feature flags for:

Maps

Payments

WhatsApp

Redis

WebSockets

Live tracking

This allows gradual rollout.

---

# 229. PHASE 1 MUST BE FUNCTIONAL

Even without:

Google Maps API

Razorpay

WhatsApp

Redis

WebSockets

the application must still function.

Use clean abstractions/mock providers where necessary.

Do not create fake integrations that pretend to be real.

---

# 230. PHASE 2 INTEGRATION READY

The following must have interfaces/placeholders:

LocationProvider

PaymentProvider

NotificationProvider

CacheProvider

RealtimeProvider

StorageProvider

This allows later integration without rewriting booking/business logic.

---

# 231. FINAL PRODUCTION ACCEPTANCE CRITERIA

The application is NOT complete until:

- Customer can book a service
- Correct vehicle type pricing is applied
- Service duration is respected
- Minimum lead time is enforced
- Operating hours are enforced
- Customer location is captured
- Correct service center is selected
- Manager receives booking
- Manager can assign captain
- Captain conflicts are prevented
- Captain cannot start too early
- Captain can check in
- Captain location is recorded
- Captain can capture camera-only evidence
- Registration number is captured
- Before/after evidence is stored
- Service completion is validated
- Customer can review
- Subscription usage works
- Subscription expiry works
- Subscription daily usage rule works
- Offers are validated server-side
- First-service abuse is prevented using mobile + vehicle registration
- Admin can modify services
- Admin can modify prices
- Admin can modify durations
- Admin can modify vehicle categories
- Admin can modify plans
- Admin can modify offers
- Admin can modify centers
- Admin can modify center radius
- Admin can modify operating hours
- Manager is restricted to their center
- Admin has global access
- Audit logs exist
- Historical pricing is preserved
- Duplicate bookings are prevented
- Concurrent captain assignment conflicts are prevented
- Customer cannot book past/current/too-near times
- Service cannot exceed closing time
- Out-of-area bookings are handled correctly
- Reassignment works
- Rescheduling works
- Cancellation works
- Attendance works
- CRM works
- Analytics work
- Responsive UI works
- Authentication works
- RBAC works
- No secrets are hardcoded
- No major business rule exists only on frontend
- No critical data is lost when configuration changes

---

# 232. DEVELOPMENT EXECUTION RULE

Do not immediately generate thousands of disconnected files.

First inspect the repository.

Understand:

- Existing code
- Existing dependencies
- Existing architecture
- Existing database
- Existing routes
- Existing components
- Existing environment variables

If the repository already contains useful code, reuse and refactor it rather than unnecessarily rebuilding everything.

Do not destroy working functionality without reason.

---

# 233. BEFORE IMPLEMENTATION

Create an internal implementation plan covering:

1. Architecture
2. Database models
3. Authentication
4. RBAC
5. Core services
6. Customer experience
7. Booking engine
8. Location allocation
9. Captain workflow
10. Manager workflow
11. Admin workflow
12. Subscription engine
13. Offer engine
14. CRM
15. Analytics
16. Audit
17. Testing

Then implement systematically.

---

# 234. DO NOT ASK FOR UNNECESSARY CLARIFICATIONS

Where requirements are explicitly defined, implement them.

Where a business rule genuinely needs a value that has not been provided:

- Make it configurable
- Choose a sensible default
- Clearly isolate the default in configuration
- Do not hardcode it inside business logic

Example:

Default captain early-start window = 30 minutes.

Store this as configuration.

---

# 235. QUALITY BAR

The final application should look and behave like a serious commercial SaaS/product platform.

It must NOT look like:

- A college project
- A generic React dashboard
- A template with random cards
- An AI-generated landing page
- A CRUD demo
- A collection of unrelated pages

The customer should feel:

"This is a professional vehicle-care company."

The manager should feel:

"I can run my service center from here."

The captain should feel:

"I know exactly what job I need to do next."

The administrator should feel:

"I can control the entire business from this system."

---

# 236. MOST IMPORTANT PRODUCT PRINCIPLE

DO NOT CONFUSE THE CUSTOMER.

Customer booking should feel like:

Vehicle → Service → Time → Location → Details → Confirm

Everything else should happen behind the scenes.

The complexity belongs in the platform's operational engine, not in the customer's face.

---

# 237. FINAL INSTRUCTION

Build this system as a **production-grade multi-service-center doorstep vehicle-care platform**.

Do not reduce the requirements to a simple booking website.

Do not hardcode business rules.

Do not create fake functionality.

Do not ignore concurrency.

Do not trust frontend calculations.

Do not allow unauthorized cross-store access.

Do not allow overlapping captain assignments.

Do not allow invalid time bookings.

Do not allow subscription abuse.

Do not allow first-service offer abuse.

Do not allow evidence to be uploaded from the gallery when camera capture is required.

Do not allow completion without required evidence.

Do not lose historical pricing/configuration.

Do not make Redis, Maps, Razorpay, WhatsApp, or WebSockets mandatory dependencies at this stage.

Build clean abstractions for those future integrations.

Make the platform:

SIMPLE

FAST

PROFESSIONAL

CONFIGURABLE

LOCATION-AWARE

AUDITABLE

SECURE

SCALABLE

MAINTAINABLE

The final result should be a serious foundation capable of growing from one service center to a large multi-city operation without requiring a complete architectural rewrite.

# END OF MASTER SYSTEM PROMPT