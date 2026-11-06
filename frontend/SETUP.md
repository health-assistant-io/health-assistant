# Health Assistant - Frontend Setup

## Installation

```bash
cd frontend
pnpm install
```

## Configuration

Create `.env` file use `.env.example` as template



## Development Server

```bash
pnpm run dev
```

## Build for Production

```bash
pnpm run build
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