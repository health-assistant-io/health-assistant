import { useTranslation } from 'react-i18next';
import { Globe, Linkedin, Mail } from 'lucide-react';
import { AboutPanel, HealthMark } from '@neuronection/assistant-ui';
import packageJson from '../../../package.json';
import { PageHeader } from '../../components/ui/PageHeader';
import { useSettingsStore } from '../../store/slices/settingsSlice';

const AboutPage = () => {
  const { t } = useTranslation();
  const theme = useSettingsStore(state => state.theme);
  const logoTheme = theme === 'dark' ? 'dark' : 'light';

  return (
    <div className="w-full max-w-4xl mx-auto pb-20">
      <PageHeader
        title={t('common.about')}
        icon={<HealthMark size={24} theme={logoTheme} />}
      />

      <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
        <AboutPanel
          appName="Health Assistant"
          familyCurrent="health"
          tagline="Universal Health Data Platform"
          description="Health Assistant is a self-hosted, privacy-first platform designed to empower individuals with control over their medical data. Inspired by the philosophy of local control and open standards, it centralizes health records, analyzes biomarkers, and provides intelligent insights while keeping your sensitive data exactly where it belongs: in your hands."
          version={packageJson.version}
          license={{
            name: 'Apache License 2.0',
            href: 'https://www.apache.org/licenses/LICENSE-2.0',
          }}
          linksTitle="Contact & Connect"
          links={[
            { group: 'Project', href: 'https://health-assistant.io', label: 'Website', subtitle: 'health-assistant.io', icon: Globe },
            { group: 'Project', href: 'https://www.linkedin.com/company/134583947', label: 'LinkedIn', subtitle: 'Health Assistant', icon: Linkedin },
            { group: 'Project', copyValue: 'hello@health-assistant.io', label: 'hello@health-assistant.io', subtitle: 'Click to copy', icon: Mail },
            { group: 'Creator', href: 'https://www.linkedin.com/in/ilias-chatzopoulos-aabb22163/', label: 'LinkedIn', subtitle: 'Ilias Chatzopoulos', icon: Linkedin },
            { group: 'Creator', copyValue: 'constliakos@gmail.com', label: 'constliakos@gmail.com', subtitle: 'Click to copy', icon: Mail },
          ]}
          creator={{
            name: 'Ilias Chatzopoulos',
            role: 'Founder & Lead Architect',
            href: 'https://github.com/constLiakos',
          }}
          tech={[
            'FastAPI',
            'Celery + Redis',
            'PostgreSQL + TimescaleDB',
            'HL7 FHIR',
            'React 18 + Vite (PWA)',
            'Android companion (Kotlin)',
            'Docker Compose',
            'Apache-2.0',
          ]}
          note={{
            tone: 'warning',
            title: 'Medical Disclaimer',
            children:
              'This software is for informational and wellness purposes only. It does NOT provide medical diagnosis or act as a substitute for professional medical care. Always consult certified medical professionals for health advice, diagnoses, or before making any medical decisions based on the software\u2019s outputs.',
          }}
          copyright="© 2026 Neuronection"
          theme={logoTheme}
        />
      </div>
    </div>
  );
};

export default AboutPage;
