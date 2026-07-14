import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Bell, Plus, Trash2, Clock, Calendar, ShieldCheck } from 'lucide-react';
import { notificationService, NotificationTrigger } from '../../services/notificationService';
import { usePatientStore } from '../../store/slices/patientSlice';

interface Props {
  medicationId: string;
  medicationName: string;
}

export function MedicationReminders({ medicationId, medicationName }: Props) {
  const { t } = useTranslation();
  const { currentPatient } = usePatientStore();
  const [triggers, setTriggers] = useState<NotificationTrigger[]>([]);
  const [loading, setLoading] = useState(false);
  const [showAddForm, setShowAddForm] = useState(false);
  
  // Form state
  const [time, setTime] = useState('09:00');
  const [days, setDays] = useState(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']);

  useEffect(() => {
    // In a real app, we'd fetch existing triggers for this reference_id
    // For now, let's just use a local state or fetch from a general list
  }, [medicationId]);

  const handleAddTrigger = async () => {
    if (!currentPatient?.id) return;
    setLoading(true);
    try {
      await notificationService.createTrigger({
        patient_id: currentPatient.id,
        title: `Reminder: ${medicationName}`,
        body: `It's time to take your scheduled dose of ${medicationName}.`,
        notification_type: 'medication_reminder',
        trigger_type: 'recurring',
        config: {
          at: time,
          days,
          medication_id: medicationId
        },
        reference_id: medicationId
      });
      setShowAddForm(false);
      // Refresh list logic here
    } catch (error) {
      console.error('Failed to create reminder:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-4 sm:p-6 mt-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-bold text-brand-navy dark:text-dark-text flex items-center">
          <Bell className="w-5 h-5 mr-2 text-indigo-500" />
          {t('common.smart_reminders')}
        </h2>
        <button 
          onClick={() => setShowAddForm(!showAddForm)}
          className="p-1.5 bg-indigo-50 dark:bg-indigo-900/20 text-indigo-600 dark:text-indigo-400 rounded-lg hover:bg-indigo-100 transition-colors"
        >
          <Plus className="w-4 h-4" />
        </button>
      </div>

      {showAddForm && (
        <div className="mb-6 p-4 bg-gray-50 dark:bg-dark-bg rounded-xl border border-gray-100 dark:border-dark-border space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-bold text-gray-400 uppercase mb-2">{t('common.reminder_time')}</label>
              <div className="relative">
                <Clock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                <input 
                  type="time" 
                  value={time}
                  onChange={(e) => setTime(e.target.value)}
                  className="w-full pl-10 pr-4 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-lg text-sm"
                />
              </div>
            </div>
            <div>
              <label className="block text-xs font-bold text-gray-400 uppercase mb-2">{t('common.reminder_days')}</label>
              <div className="flex flex-wrap gap-1">
                {['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'].map(day => (
                  <button
                    key={day}
                    onClick={() => setDays(prev => prev.includes(day) ? prev.filter(d => d !== day) : [...prev, day])}
                    className={`px-2 py-1 text-[10px] font-bold rounded-md border transition-all ${days.includes(day) ? 'bg-indigo-600 border-indigo-600 text-white' : 'bg-white dark:bg-dark-surface border-gray-200 dark:border-dark-border text-gray-400'}`}
                  >
                    {day.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>
          </div>
          <div className="flex justify-end space-x-2">
            <button 
              onClick={() => setShowAddForm(false)}
              className="px-4 py-2 text-sm font-semibold text-gray-500 hover:text-gray-700"
            >
              {t('common.cancel')}
            </button>
            <button 
              onClick={handleAddTrigger}
              disabled={loading}
              className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-bold shadow-md shadow-indigo-200 hover:bg-indigo-700 disabled:opacity-50"
            >
              {loading ? t('common.loading') : t('common.save')}
            </button>
          </div>
        </div>
      )}

      <div className="space-y-3">
        {/* Placeholder for real triggers */}
        <div className="flex items-center justify-between p-3 bg-indigo-50/30 dark:bg-indigo-900/5 border border-indigo-100 dark:border-indigo-900/20 rounded-xl">
          <div className="flex items-center space-x-3">
            <div className="w-10 h-10 bg-white dark:bg-dark-surface rounded-full flex items-center justify-center text-indigo-600 shadow-sm border border-indigo-50 dark:border-indigo-900/30">
              <Clock className="w-5 h-5" />
            </div>
            <div>
              <p className="text-sm font-bold text-gray-900 dark:text-dark-text">Daily Routine</p>
              <p className="text-xs text-indigo-600 dark:text-indigo-400 font-medium flex items-center">
                <Calendar className="w-3 h-3 mr-1" />
                Mon, Wed, Fri at 09:00 AM
              </p>
            </div>
          </div>
          <div className="flex items-center space-x-2">
            <div className="px-2 py-1 bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 text-[10px] font-bold rounded uppercase flex items-center">
              <ShieldCheck className="w-3 h-3 mr-1" />
              Active
            </div>
            <button className="p-1.5 text-gray-300 hover:text-red-500 transition-colors">
              <Trash2 className="w-4 h-4" />
            </button>
          </div>
        </div>

        {triggers.length === 0 && !showAddForm && (
          <div className="text-center py-6 border-2 border-dashed border-gray-100 dark:border-dark-border rounded-2xl">
            <Bell className="w-8 h-8 text-gray-200 dark:text-dark-border mx-auto mb-2" />
            <p className="text-xs text-gray-400">{t('medications.no_active_reminders') || 'No custom reminders set for this medication'}</p>
          </div>
        )}
      </div>
    </div>
  );
}
