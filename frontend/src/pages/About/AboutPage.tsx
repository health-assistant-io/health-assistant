import { useTranslation } from 'react-i18next';
import { PageHeader } from '../../components/ui/PageHeader';
import { useSettingsStore } from '../../store/slices/settingsSlice';
import { Info, Github, Globe, Heart, ShieldCheck, Code, Award } from 'lucide-react';

const AboutPage = () => {
  const { t } = useTranslation();
  const theme = useSettingsStore(state => state.theme);

  return (
    <div className="w-full max-w-4xl mx-auto pb-20">
      <PageHeader
        title={t('common.about')}
        subtitle="Universal Health Data Platform"
        icon={<img src={theme === 'dark' ? '/icon.svg' : '/icon-light.svg'} className="w-6 h-6" alt="About" />}
      />

      <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
        {/* Main Mission */}
        <section className="bg-white dark:bg-dark-surface p-8 rounded-3xl border border-gray-100 dark:border-dark-border shadow-sm">
          <div className="flex items-start gap-6">
            <div className="w-14 h-14 bg-blue-50 dark:bg-blue-900/20 rounded-2xl flex items-center justify-center shrink-0">
              <Heart className="w-8 h-8 text-blue-600 dark:text-blue-400" />
            </div>
            <div>
              <h2 className="text-xl font-black text-[#1a2b4b] dark:text-white mb-2">Our Mission</h2>
              <p className="text-gray-600 dark:text-dark-muted leading-relaxed">
                Health Assistant is a self-hosted, privacy-first platform designed to empower individuals with control over their medical data. 
                Inspired by the philosophy of local control and open standards, it centralizes health records, analyzes biomarkers, 
                and provides intelligent insights while keeping your sensitive data exactly where it belongs: in your hands.
              </p>
            </div>
          </div>
        </section>

        {/* Credits Section */}
        <section className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="bg-white dark:bg-dark-surface p-8 rounded-3xl border border-gray-100 dark:border-dark-border shadow-sm">
            <div className="flex items-center gap-4 mb-6">
              <div className="w-12 h-12 bg-indigo-50 dark:bg-indigo-900/20 rounded-xl flex items-center justify-center">
                <Code className="w-6 h-6 text-indigo-600 dark:text-indigo-400" />
              </div>
              <h2 className="text-xl font-black text-[#1a2b4b] dark:text-white">Created By</h2>
            </div>
            <div className="space-y-4">
              <div>
                <h3 className="font-bold text-gray-900 dark:text-white">Ilias Chatzopoulos</h3>
                <p className="text-sm text-gray-500 dark:text-dark-muted mb-4">Founder & Lead Architect</p>
                
                <div className="flex flex-col gap-3">
                  <a 
                    href="https://github.com/constLiakos" 
                    target="_blank" 
                    rel="noopener noreferrer"
                    className="flex items-center gap-3 text-sm text-gray-600 dark:text-dark-text hover:text-blue-600 dark:hover:text-blue-400 transition-colors"
                  >
                    <Github className="w-4 h-4" />
                    <span>constLiakos</span>
                  </a>
                  <a 
                    href="https://health-assistant.io" 
                    target="_blank" 
                    rel="noopener noreferrer"
                    className="flex items-center gap-3 text-sm text-gray-600 dark:text-dark-text hover:text-blue-600 dark:hover:text-blue-400 transition-colors"
                  >
                    <Globe className="w-4 h-4" />
                    <span>health-assistant.io</span>
                  </a>
                  <a 
                    href="https://github.com/health-assistant-io/health-assistant" 
                    target="_blank" 
                    rel="noopener noreferrer"
                    className="flex items-center gap-3 text-sm text-gray-600 dark:text-dark-text hover:text-blue-600 dark:hover:text-blue-400 transition-colors"
                  >
                    <Github className="w-4 h-4" />
                    <span>Official Repository</span>
                  </a>
                </div>
              </div>
            </div>
          </div>

          <div className="bg-white dark:bg-dark-surface p-8 rounded-3xl border border-gray-100 dark:border-dark-border shadow-sm">
            <div className="flex items-center gap-4 mb-6">
              <div className="w-12 h-12 bg-emerald-50 dark:bg-emerald-900/20 rounded-xl flex items-center justify-center">
                <ShieldCheck className="w-6 h-6 text-emerald-600 dark:text-emerald-400" />
              </div>
              <h2 className="text-xl font-black text-[#1a2b4b] dark:text-white">License & Open Source</h2>
            </div>
            <p className="text-gray-600 dark:text-dark-muted text-sm leading-relaxed mb-6">
              Health Assistant is proud to be open-source. We believe that health software should be transparent, 
              auditable, and accessible to everyone.
            </p>
            <div className="flex items-center justify-between p-4 bg-gray-50 dark:bg-dark-bg rounded-2xl border border-gray-100 dark:border-dark-border">
              <div className="flex items-center gap-3">
                <Award className="w-5 h-5 text-amber-500" />
                <span className="font-bold text-gray-700 dark:text-white">Apache License 2.0</span>
              </div>
              <a 
                href="https://www.apache.org/licenses/LICENSE-2.0" 
                target="_blank" 
                rel="noopener noreferrer"
                className="text-xs text-blue-600 dark:text-blue-400 hover:underline font-bold"
              >
                Read License
              </a>
            </div>
          </div>
        </section>

        {/* Disclaimer */}
        <section className="bg-amber-50/50 dark:bg-amber-900/10 p-8 rounded-3xl border border-amber-100/50 dark:border-amber-900/20">
          <h2 className="text-lg font-black text-amber-900 dark:text-amber-400 mb-3 uppercase tracking-wider">Medical Disclaimer</h2>
          <p className="text-amber-800/80 dark:text-amber-400/80 text-sm leading-relaxed">
            This software is for informational and wellness purposes only. It does NOT provide medical diagnosis 
            or act as a substitute for professional medical care. Always consult certified medical professionals 
            for health advice, diagnoses, or before making any medical decisions based on the software's outputs.
          </p>
        </section>

        {/* Version Info */}
        <div className="text-center pt-4">
          <p className="text-gray-400 dark:text-dark-muted text-xs">
            Health Assistant Version 0.2.0<br />
            © 2026 Ilias Chatzopoulos. All rights reserved.
          </p>
        </div>
      </div>
    </div>
  );
};

export default AboutPage;
