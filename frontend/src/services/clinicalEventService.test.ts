import { describe, it, expect, vi, beforeEach } from 'vitest';
import api from '../api/axios';
import { 
  getEventTypes, 
  getPatientEvents, 
  createEvent, 
  updateEvent, 
  getEvent, 
  deleteEvent, 
  linkExaminationToEvent
} from './clinicalEventService';

describe('clinicalEventService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    
    // Mock the axios instance methods
    api.get = vi.fn();
    api.post = vi.fn();
    api.put = vi.fn();
    api.delete = vi.fn();
  });

  it('getEventTypes calls the correct endpoint', async () => {
    const mockData = [{ id: '1', name: 'Pregnancy' }];
    (api.get as any).mockResolvedValueOnce({ data: mockData });

    const result = await getEventTypes();
    
    expect(api.get).toHaveBeenCalledWith('/clinical-events/types');
    expect(result).toEqual(mockData);
  });

  it('getPatientEvents calls the correct endpoint with patientId', async () => {
    const patientId = 'p123';
    const mockData = [{ id: 'e1', title: 'Test Event' }];
    (api.get as any).mockResolvedValueOnce({ data: mockData });

    const result = await getPatientEvents(patientId);
    
    expect(api.get).toHaveBeenCalledWith(`/clinical-events?patient_id=${patientId}`);
    expect(result).toEqual(mockData);
  });

  it('createEvent calls the correct endpoint with data', async () => {
    const eventData = { title: 'New Event', patient_id: 'p123' };
    const mockResponse = { id: 'e1', ...eventData };
    (api.post as any).mockResolvedValueOnce({ data: mockResponse });

    const result = await createEvent(eventData);
    
    expect(api.post).toHaveBeenCalledWith('/clinical-events', eventData);
    expect(result).toEqual(mockResponse);
  });

  it('updateEvent calls the correct endpoint', async () => {
    const eventId = 'e1';
    const updateData = { title: 'Updated Title' };
    const mockResponse = { id: eventId, ...updateData };
    (api.put as any).mockResolvedValueOnce({ data: mockResponse });

    const result = await updateEvent(eventId, updateData);
    
    expect(api.put).toHaveBeenCalledWith(`/clinical-events/${eventId}`, updateData);
    expect(result).toEqual(mockResponse);
  });

  it('getEvent calls the correct endpoint', async () => {
    const eventId = 'e1';
    const mockData = { id: eventId, title: 'Test Event' };
    (api.get as any).mockResolvedValueOnce({ data: mockData });

    const result = await getEvent(eventId);
    
    expect(api.get).toHaveBeenCalledWith(`/clinical-events/${eventId}`);
    expect(result).toEqual(mockData);
  });

  it('deleteEvent calls the correct endpoint', async () => {
    const eventId = 'e1';
    (api.delete as any).mockResolvedValueOnce({});

    await deleteEvent(eventId);
    
    expect(api.delete).toHaveBeenCalledWith(`/clinical-events/${eventId}`);
  });

  it('linkExaminationToEvent calls the correct endpoint', async () => {
    const eventId = 'e1';
    const examinationId = 'exam1';
    const reason = 'test reason';
    const mockResponse = { id: eventId };
    (api.post as any).mockResolvedValueOnce({ data: mockResponse });

    const result = await linkExaminationToEvent(eventId, examinationId, reason);
    
    expect(api.post).toHaveBeenCalledWith(`/clinical-events/${eventId}/link-examination`, {
      examination_id: examinationId,
      reason,
    });
    expect(result).toEqual(mockResponse);
  });
});
