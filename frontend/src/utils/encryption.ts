export async function generateKey(password: string, salt: string): Promise<CryptoKey> {
  const enc = new TextEncoder();
  const passwordKey = await window.crypto.subtle.importKey(
    'raw',
    enc.encode(password),
    { name: 'PBKDF2' },
    false,
    ['deriveKey']
  );

  return window.crypto.subtle.deriveKey(
    {
      name: 'PBKDF2',
      salt: enc.encode(salt),
      iterations: 100000,
      hash: 'SHA-256',
    },
    passwordKey,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt']
  );
}

export async function encryptData(data: string, key: CryptoKey): Promise<string> {
  const iv = window.crypto.getRandomValues(new Uint8Array(12));
  const enc = new TextEncoder();
  const encryptedContent = await window.crypto.subtle.encrypt(
    { name: 'AES-GCM', iv: iv },
    key,
    enc.encode(data)
  );

  const encryptedArray = new Uint8Array(encryptedContent);
  const encryptedData = new Uint8Array(iv.length + encryptedArray.length);
  encryptedData.set(iv);
  encryptedData.set(encryptedArray, iv.length);

  return btoa(String.fromCharCode(...Array.from(encryptedData)));
}

export async function decryptData(encryptedData: string, key: CryptoKey): Promise<string> {
  const encryptedBytes = Uint8Array.from(atob(encryptedData), (c) => c.charCodeAt(0));
  const iv = encryptedBytes.slice(0, 12);
  const data = encryptedBytes.slice(12);

  const decryptedContent = await window.crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: iv },
    key,
    data
  );

  return new TextDecoder().decode(decryptedContent);
}

export async function encryptFile(file: File, password: string): Promise<File> {
  const reader = new FileReader();
  const salt = window.crypto.randomUUID();
  
  return new Promise((resolve, reject) => {
    reader.onload = async () => {
      try {
        const key = await generateKey(password, salt);
        const encrypted = await encryptData(reader.result as string, key);
        
        const encryptedBlob = new Blob([salt, encrypted], { type: file.type });
        const encryptedFile = new File([encryptedBlob], `encrypted-${file.name}`, {
          type: file.type,
        });
        
        resolve(encryptedFile);
      } catch (error) {
        reject(error);
      }
    };
    
    reader.onerror = reject;
    reader.readAsText(file);
  });
}