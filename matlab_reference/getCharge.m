function varargout = getCharge(amplitude,phaseWidth,varargin)
%% Variable
% Amplitude
amplitude_mag = abs(amplitude);

% Phase Width
phaseWidth_s = phaseWidth * 1e-6;           % pulse width us to s

% Charge/Phase
amplitude_A = amplitude_mag * 1e-6;       % current uA to A
charge_A = amplitude_A * phaseWidth_s;
chargePhase = charge_A * 1e9;   % charge per phase nC/ph

%% Function
if ~isempty(varargin)
    surfaceArea = varargin{1};
    % Geometric surface area conversion
    surfaceArea_cm2 = surfaceArea * 1e-8;  % geometric surface area m2 to cm2

    % Charge Injection
    chargeInjection = charge_A * 1e3 / surfaceArea_cm2;   % charge injection mC/cm2
else
    chargeInjection = [];
end

varargout{1} = chargePhase;
varargout{2} = chargeInjection;

end