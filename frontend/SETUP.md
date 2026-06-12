# Health Assistant - Frontend Setup

## Installation

```bash
cd frontend
npm install
```

## Configuration

Create `.env` file use `.env.example` as template



## Development Server

```bash
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

## Testing

```bash
npm test
```