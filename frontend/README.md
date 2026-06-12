# Health Assistant - Frontend

## Project Structure

```
frontend/
├── public/
│   ├── index.html
│   └── manifest.json
├── src/
│   ├── api/
│   │   ├── axios.ts
│   │   └── graphql.ts
│   ├── components/
│   │   ├── layout/
│   │   │   ├── Header.tsx
│   │   │   ├── Sidebar.tsx
│   │   │   └── Layout.tsx
│   │   ├── ui/
│   │   │   ├── Button.tsx
│   │   │   ├── Input.tsx
│   │   │   ├── Card.tsx
│   │   │   └── Table.tsx
│   │   ├── charts/
│   │   │   ├── LineChart.tsx
│   │   │   ├── BarChart.tsx
│   │   │   └── ReferenceRangeChart.tsx
│   │   └── dashboard/
│   │       ├── RecentDocuments.tsx
│   │       ├── VitalStats.tsx
│   │       └── Alerts.tsx
│   ├── hooks/
│   │   ├── useAuth.ts
│   │   ├── useApi.ts
│   │   └── useWebSocket.ts
│   ├── pages/
│   │   ├── Auth/
│   │   │   ├── Login.tsx
│   │   │   └── Register.tsx
│   │   ├── Dashboard/
│   │   │   ├── Dashboard.tsx
│   │   │   └── Analytics.tsx
│   │   ├── Documents/
│   │   │   ├── DocumentList.tsx
│   │   │   ├── DocumentUpload.tsx
│   │   │   └── DocumentDetail.tsx
│   │   ├── Patients/
│   │   │   ├── PatientList.tsx
│   │   │   └── PatientDetail.tsx
│   │   ├── Wearable/
│   │   │   ├── WearableData.tsx
│   │   │   └── WearableSettings.tsx
│   │   └── Settings/
│   │       ├── Profile.tsx
│   │       └── Preferences.tsx
│   ├── services/
│   │   ├── authService.ts
│   │   ├── documentService.ts
│   │   ├── fhirService.ts
│   │   └── userService.ts
│   ├── store/
│   │   ├── slices/
│   │   │   ├── authSlice.ts
│   │   │   ├── documentSlice.ts
│   │   │   └── userSlice.ts
│   │   └── store.ts
│   ├── types/
│   │   ├── fhir.ts
│   │   ├── api.ts
│   │   └── user.ts
│   ├── utils/
│   │   ├── unitConverter.ts
│   │   ├── dateFormatter.ts
│   │   └── encryption.ts
│   ├── App.tsx
│   └── main.tsx
├── tests/
│   ├── unit/
│   └── integration/
├── Dockerfile
├── package.json
└── tsconfig.json
```

## Getting Started

```bash
cd frontend
npm install
npm run dev
```

## Build for Production

```bash
npm run build
```

## Docker Build

```bash
docker build -t health_assistant-frontend .
docker run -p 3000:3000 health_assistant-frontend
```