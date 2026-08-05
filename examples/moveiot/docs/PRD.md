# MOVE — Product Requirements Document (PRD)

**Mobile Observatory of Vehicular Emissions** | Observatorio Móvil de Emisiones Vehiculares

---

## 1. Executive Summary

MOVE is an IoT-based mobile environmental monitoring system designed to measure and analyze vehicular emissions in real-time. The platform combines vehicle detection (using AI/YOLO) with air quality sensors to correlate emissions data with traffic patterns, providing insights into environmental impact across monitored regions.

**Primary Goal**: Enable environmental agencies, researchers, and urban planners to understand vehicular emission patterns and their spatial/temporal correlations with real-time vehicle traffic data.

---

## 2. Product Vision

### What MOVE Does
- **Real-Time Monitoring**: Captures air quality and vehicle detection data simultaneously
- **Spatial Analysis**: Maps emission hotspots by geographic location
- **Temporal Analysis**: Identifies patterns over time (hourly, daily, weekly)
- **Correlation Analysis**: Links vehicle traffic volume to measured pollutants (CO₂, particles, etc.)
- **Data Export**: Generates reports and exports datasets for external analysis

### Target Users
- Environmental agencies and regulators
- Urban planners and city governments
- Environmental researchers and universities
- Public health organizations
- Community-driven environmental monitoring groups

---

## 3. Core Features

### 3.1 Dashboard
- **Real-Time Overview**: Live vehicle count, current pollution levels, active monitoring devices
- **Device Status**: Map view showing active/inactive monitoring stations
- **Quick Metrics**: Cards displaying key indicators (average emissions, vehicle count, recent detections)
- **Loading States**: Smooth loading animations while data is fetched

### 3.2 Vehicle Detection & Analysis
- **Live Detection Feed**: Real-time vehicle count via YOLO v11 inference
- **Vehicle Counting**: Tracks total vehicles detected per time window
- **Temporal Breakdown**: Visualization of vehicle count by hour/day
- **Location-Based Analysis**: Aggregates detection data by monitoring device location

### 3.3 Air Quality Analysis
- **Pollutant Tracking**: Measures CO₂, particulate matter, and other sensors
- **Temporal Graphs**: Time-series visualization of pollution levels
- **Statistical Summary**: Mean, median, and trend analysis
- **Location Heatmap**: Geographic display of pollution hotspots

### 3.4 Correlation Analysis
- **Cross-Variable Correlation**: Analyzes relationship between vehicle count and pollutants
- **Scatter Plots**: Visual correlation matrices (vehicle count vs. each pollutant)
- **Lag Analysis**: Identifies time-lag effects (does traffic lead emissions by N minutes?)
- **Statistical Metrics**: Correlation coefficients and significance testing

### 3.5 Data Management & Export
- **Filtering**: Filter data by date range, location, and device
- **PDF Reports**: Export analysis visualizations and statistics as PDFs
- **Raw Data Export**: Download datasets in CSV/JSON for external analysis
- **Audit Trail**: Track when data was accessed and exported

### 3.6 Authentication & Authorization
- **Keycloak Integration**: OAuth2/OIDC identity management
- **Role-Based Access**: Different user roles (admin, analyst, viewer)
- **Session Management**: Secure token handling in sessionStorage
- **JWT Validation**: Server-side token verification

---

## 4. Technical Requirements

### 4.1 Architecture

```
┌─────────────────────────────────────────────────────┐
│  Frontend (Angular 20 + Tailwind CSS)               │
│  ├─ Dashboard                                        │
│  ├─ Analysis Components (Temporal, Spatial, etc.)   │
│  ├─ Device Management                               │
│  └─ Export & Reporting                              │
└────────────────┬────────────────────────────────────┘
                 │ REST API (Bearer Token Auth)
┌────────────────▼────────────────────────────────────┐
│  Backend (Spring Boot 3 + Java 21)                  │
│  ├─ Vehicle Detection Service Proxy                 │
│  ├─ Sensor Data API                                 │
│  ├─ Analysis & Correlation Computation              │
│  ├─ User & Device Management                        │
│  └─ Report Generation                               │
└────────────────┬────────────────────────────────────┘
                 │
        ┌────────┼────────┐
        │        │        │
        ▼        ▼        ▼
   PostgreSQL Keycloak  Python AI
   (Main DB)  (Identity) (YOLO v11)
                         (OpenCV)
```

### 4.2 Tech Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| **Frontend** | Angular | 20.0.6 |
| **Styling** | Tailwind CSS | 4.1.17 |
| **Charts/Maps** | amCharts 5, Google Maps, ApexCharts | Latest |
| **Auth** | Keycloak JS | 26.2.3 |
| **PDF Export** | html2canvas, jsPDF | Latest |
| **Backend** | Spring Boot | 3.x |
| **Language** | Java | 21 (LTS) |
| **Database** | PostgreSQL | 15+ |
| **Container** | Docker, Docker Compose | Latest |
| **AI/Detection** | Python 3.10+, YOLO v11, PyTorch, OpenCV | Latest |

### 4.3 API Contract (Backend Endpoints)

#### Vehicles
- `GET /api/vehicles` — List detected vehicles (with filters)
- `GET /api/vehicles/count` — Aggregate vehicle count by time window
- `GET /api/vehicles/by-location` — Vehicle count per monitoring device

#### Sensors (Air Quality)
- `GET /api/sensors` — List active sensors/devices
- `GET /api/sensors/{id}/readings` — Historical readings for a sensor
- `GET /api/sensors/readings/aggregate` — Multi-sensor aggregation

#### Analysis
- `GET /api/analysis/correlation` — Compute correlation matrix
- `GET /api/analysis/lag` — Lag analysis (time-shift correlation)
- `GET /api/analysis/heatmap` — Location-based pollution aggregation
- `GET /api/analysis/temporal` — Time-series aggregation

#### Export
- `POST /api/export/pdf` — Generate PDF report
- `GET /api/export/csv` — Export raw data as CSV
- `GET /api/export/json` — Export raw data as JSON

#### User & Auth
- `GET /api/auth/user` — Current authenticated user
- `GET /api/auth/profile` — User profile and permissions

### 4.4 Configuration (Environment Variables)

| Variable | Purpose | Example |
|----------|---------|---------|
| `KEYCLOAK_ISSUER_URI` | Public Keycloak URL (must match JWT iss) | `https://auth.example.com/realms/move` |
| `KEYCLOAK_JWK_SET_URI` | Internal Keycloak key endpoint | `http://keycloak:8080/realms/move/protocol/openid-connect/certs` |
| `CORS_ALLOWED_ORIGINS` | Frontend origin(s) | `http://localhost:4200,https://app.example.com` |
| `PYTHON_SERVICE_URL` | Detection service URL (from backend) | `http://host.docker.internal:5000` |
| `PROVISIONING_FACTORY_TOKEN` | ESP32 self-registration secret | (secure token) |
| `BACKEND_URL` | URL detection service uses to POST results | `http://backend:8080` |
| `DATABASE_URL` | PostgreSQL connection string | `jdbc:postgresql://move_db:5432/move_db` |

---

## 5. User Workflows

### 5.1 Analyst Reviews Vehicle-Pollution Correlation
1. Opens Dashboard, sees latest metrics
2. Navigates to **Correlation Analysis**
3. Selects date range (last 7 days)
4. Selects one or more locations
5. Views correlation matrix heatmap
6. Runs **Lag Analysis** to see if traffic leads emissions
7. Exports findings as PDF report

### 5.2 Researcher Downloads Raw Data
1. Logs in with research credentials
2. Navigates to **Data Export**
3. Filters by date, location, pollutant type
4. Chooses export format (CSV/JSON)
5. Downloads file for external statistical analysis

### 5.3 Admin Manages Monitoring Devices
1. Views all devices in **Device Management**
2. Monitors device health (battery, last data received)
3. Deactivates malfunctioning sensor
4. Updates device metadata (location, name)

---

## 6. Non-Functional Requirements

### Performance
- **API Response Time**: <500ms for simple queries, <2s for analysis computations
- **Chart Rendering**: <1s for graphs with 1000+ data points
- **Concurrent Users**: Support 50+ simultaneous users
- **Data Freshness**: Real-time data within 30 seconds of collection

### Scalability
- Horizontal scaling of backend services via Docker
- Database indexing for efficient time-range queries
- Caching of computed analysis results

### Security
- All API endpoints require valid JWT
- Role-based access control (RBAC)
- HTTPS/TLS for all external communication
- Secure credential storage (environment variables, secrets)
- CORS policy restricting to authorized origins
- No sensitive data logged (PII, tokens)

### Reliability
- 99% uptime target for core services
- Graceful degradation (show cached data if real-time feed unavailable)
- Database backups and point-in-time recovery
- Health checks for all microservices

### Usability
- Responsive UI (mobile-friendly dashboard)
- Loading spinners for long operations
- Clear error messages and user feedback
- Consistent component design and styling
- Accessibility compliance (WCAG 2.1 AA target)

---

## 7. Data Model (Key Entities)

### Vehicle Detection
```
Detection {
  id: UUID
  timestamp: DateTime
  sensorId: String
  vehicleCount: Integer
  latitude: Double
  longitude: Double
  confidence: Float
}
```

### Air Quality Sensor
```
SensorReading {
  id: UUID
  timestamp: DateTime
  sensorId: String
  co2: Float (ppm)
  pm25: Float (μg/m³)
  pm10: Float (μg/m³)
  temperature: Float (°C)
  humidity: Float (%)
  location: Point (lat, long)
}
```

### Device
```
Device {
  id: String
  name: String
  type: ENUM(VEHICLE_DETECTOR, AIR_QUALITY_SENSOR, COMBINED)
  status: ENUM(ACTIVE, INACTIVE, PROVISIONAL)
  lastDataReceived: DateTime
  location: Point
  metadata: JSON
}
```

---

## 8. Deployment Strategy

### Development
- Local: Docker Compose with core services + native Python detection service
- Frontend: `npm start` (Angular dev server at localhost:4200)

### Staging / Production
- Full Docker Compose stack with all profiles
- Cloudflare Tunnel for public access
- Environment-specific `.env` files
- Database migrations managed by Spring Boot (`ddl-auto=update`)

### Monitoring
- Health check endpoints on all services
- Container restart policies
- Log aggregation (via Docker logs or ELK stack)
- Prometheus metrics (future enhancement)

---

## 9. Success Metrics

### Product Success
- **User Engagement**: >80% of analysts use correlation analysis feature monthly
- **Data Quality**: >95% of readings have valid, non-null values
- **System Uptime**: >99% availability of dashboard and API
- **Report Generation**: <5s PDF export for typical analysis scope

### Business Success
- **Adoption**: Partner with ≥3 municipal governments within 12 months
- **Data Accuracy**: Validation studies show ±10% accuracy vs. reference equipment
- **Cost Efficiency**: Operating cost <$500/month per monitoring device

---

## 10. Constraints & Assumptions

### Constraints
- Backend communicates with Python detection service via HTTP (not async/queue)
- Keycloak is mandatory for authentication (no local user database)
- PostgreSQL is the sole datastore (no multi-database support)
- Deployment assumes Docker/Linux environment

### Assumptions
- IoT devices reliably transmit data to backend
- Keycloak is running and properly configured before app starts
- Database backups are handled externally (not in-app)
- Historical data volumes <10GB for acceptable query performance

---

## 11. Roadmap (Post-MVP)

### Phase 2 (Future)
- Real-time alerting (SMS/email when pollution exceeds threshold)
- Mobile app for data collection via smartphone sensors
- Integration with public transit data
- Machine learning predictions of future pollution levels

### Phase 3 (Future)
- Distributed processing for multi-city deployments
- GraphQL API for flexible querying
- Advanced ML models for anomaly detection
- Community contributions (citizen science platform)

---

## 12. Glossary

| Term | Definition |
|------|-----------|
| **YOLO v11** | Real-time object detection model (YOLOv11 is the latest version) |
| **Air Quality Index (AQI)** | Standardized measure of pollution levels (EPA standard in US) |
| **Correlation** | Statistical measure of relationship between two variables (-1 to +1) |
| **Lag** | Time delay between cause (traffic) and effect (emissions) |
| **Heatmap** | 2D visualization where color intensity represents data value |
| **JWT** | JSON Web Token, used for stateless API authentication |
| **OAuth2 / OIDC** | Industry-standard protocols for authentication and authorization |

---

## Document History

| Date | Version | Author | Changes |
|------|---------|--------|---------|
| 2026-05-11 | 1.0 | Victor Narvaez | Initial PRD creation |

---

**Status**: Active Development  
**Last Updated**: 2026-05-11  
**Next Review**: 2026-06-11
