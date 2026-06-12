# Task Progress Indicator Implementation

**Date**: March 17, 2026

## Overview

Implemented a comprehensive task progress indicator system that shows pending AI/OCR tasks at both examination and document levels. The indicator only appears when there are pending tasks, providing users with a clear overview of processing status.

## Components Created

### 1. TaskProgressIndicator Component
**File**: `frontend/src/components/ui/TaskProgressIndicator.tsx`

**Features**:
- Shows examination extraction status and progress
- Shows document OCR processing status
- Compact mode for lists, full mode for details
- Auto-hides when no pending tasks
- Animated indicators (pulse, spin)

**Props**:
- `examinationStatus`: Current extraction status (e.g., "processing", "analyzing_text")
- `examinationProgress`: Progress percentage (0-100)
- `documents`: Array of documents to check OCR status
- `compact`: Boolean for compact/list view mode

## Integration Points

### 1. ExaminationList Page (`/examinations`)
**File**: `frontend/src/pages/Examinations/ExaminationList.tsx`

**Locations**:
1. **Timeline cards** - Compact mode for each examination
2. **Preview panel** - Full mode for selected examination details

**Timeline Card Display**: Compact mode showing:
- Blue badge: "Extracting (XX%)" with Activity icon (when extraction pending)

**Preview Panel Display**: Full mode showing:
- Examination extraction progress bar
- Document OCR progress (documents are loaded for selected exam)
- Detailed status labels

**Code** (Preview Panel):
```tsx
{selectedExam && (
  <div className="mb-6">
    <TaskProgressIndicator 
      examinationStatus={selectedExam.extraction_status}
      examinationProgress={selectedExam.extraction_progress}
      documents={examDocuments}
    />
  </div>
)}
```

**Note**: Documents are loaded for the selected examination via `getExaminationDocuments(selectedExam.id)`, so the preview panel can show both examination and document status.

### 2. ExaminationDetail Page
**File**: `frontend/src/pages/Examinations/ExaminationDetail.tsx`

**Locations**:
1. **Header section** - Full progress indicator after action buttons
2. **Documents table** - Per-document OCR status with "OCR pending" label

**Display**:
- Full progress bar for examination extraction
- Progress bar for document OCR completion
- Animated pulse effects
- Detailed status labels

**Code**:
```tsx
<div className="mb-8">
  <TaskProgressIndicator 
    examinationStatus={examination?.extraction_status}
    examinationProgress={examination?.extraction_progress}
    documents={documents}
  />
</div>
```

## Visual Design

### Compact Mode (ExaminationList)
```
[📊 Activity] Extracting (45%)  [📄 FileText] 2/5 OCR
```
- Blue badge for extraction with pulse animation
- Indigo badge for OCR with count display
- Small icons, minimal spacing

### Full Mode (ExaminationDetail)
```
┌────────────────────────────────────────────┐
│ [📊] Processing Tasks              [🕐]   │
│ AI pipeline is running                     │
│                                            │
│ analyzing_text                    45%      │
│ ████████████──────────────────────          │
│                                            │
│ Document Processing               2/5      │
│ ████████──────────────────────────          │
│ 3 pending [⚠️]                             │
└────────────────────────────────────────────┘
```
- Blue container with border
- Two progress bars (extraction + OCR)
- Animated icons
- Detailed labels and percentages

## Status Tracking

### Examination Level
- **Field**: `extraction_status` (string)
- **Progress**: `extraction_progress` (integer 0-100)
- **States**: processing, aggregating, analyzing_text, defining_ontology, persisting_results, completed, failed

### Document Level
- **Field**: `status` (string)
- **States**: uploaded, processing, completed, failed
- **Tracking**: Counts pending vs completed documents

## Behavior

### Auto-Hide
Component renders `null` when:
- No examination extraction pending
- No document OCR pending
- All tasks completed/failed

### Live Updates
- ExaminationDetail polls every 3 seconds during processing
- Progress bars animate smoothly
- Status badges pulse when active

### Stall Detection
- Existing stall detection (5 minutes) in ExaminationDetail
- Warning prompt to restart analysis
- Amber warning banner

## User Experience

### Benefits
1. **Visibility**: Users immediately see what's processing
2. **Progress**: Clear percentage and progress bars
3. **Context**: Shows both exam + document status together
4. **Action**: Prompts to restart stalled processes
5. **Clean**: Auto-hides when complete

### Design Alignment
- Matches "Run AI Analysis" button styling
- Uses same color palette (blue, indigo)
- Consistent animation (pulse, spin)
- Rounded borders, shadow effects

## Testing

### Manual Testing Steps
1. Upload document → See OCR pending indicator
2. Run AI analysis → See extraction progress bar
3. Wait for completion → Indicator auto-hides
4. Check ExaminationList → Compact badges appear
5. Check ExaminationDetail → Full progress view

## Files Modified

### Created
- `frontend/src/components/ui/TaskProgressIndicator.tsx`

### Updated
- `frontend/src/pages/Examinations/ExaminationList.tsx`
  - Added import
  - Added component in timeline cards
  
- `frontend/src/pages/Examinations/ExaminationDetail.tsx`
  - Added import
  - Added component in header
  - Enhanced document table status column
  - Cleaned unused imports (Tag, Users)

## Backend Dependencies

### Examination Model
```python
extraction_status = Column(String(50), nullable=True)
extraction_progress = Column(Integer, default=0)
```

### Document Model
```python
status = Column(String, default="uploaded")
```

## Next Steps

### Potential Enhancements
1. **Tooltip Details**: Hover tooltips explaining each status
2. **Estimated Time**: Show ETA for completion
3. **Task Queue**: Show number of tasks in queue
4. **Notifications**: Push notification when complete
5. **Retry Button**: Quick retry from progress indicator

### Performance
- Current polling: 3 seconds
- Consider WebSocket for real-time updates
- Batch status checks for multiple exams

## Conclusion

The Task Progress Indicator provides users with clear, actionable visibility into AI/OCR processing tasks. It appears only when needed, uses consistent design patterns, and integrates seamlessly into both examination list and detail views.