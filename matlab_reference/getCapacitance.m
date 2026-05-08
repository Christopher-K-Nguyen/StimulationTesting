function varargout = getCapacitance(File,time,voltage,varargin)
fprintf('Getting capacitance...\n');

%% Constants
F_TO_NF = 1e9;

%% Channels
channel_arr = [File.Data(:).ActiveChannel];
groupNum = length(channel_arr);
channelNum = channel_arr(groupNum);
testChannel_arr = File.Parameters.Channels.Test;
channel_idx = find(testChannel_arr == channelNum,1);

%% Environment
environment = File.Parameters.Environment;
isAnimal = contains2(environment,'Animal');
if isAnimal
    capCheck = 0.98;
else
    capCheck = 0.999;
end

%% Voltage
isAtVoltageCompliance = any(abs(voltage) > 9);

%% Pattern
% polarity = File.Parameters.Polarity;
amplitude1 = File.Parameters.Amplitude1(channel_idx);
amplitude_mag = abs(amplitude1);
amplitude_A = amplitude_mag * 1e-6;
phaseWidth1 = File.Parameters.PhaseWidth1;
if phaseWidth1 >= 100
    cutoff_idx = 25;
else
    cutoff_idx = 10;
end

%% Indices
% if isempty(varargin)
    fprintf('\t');
    [~,~,access_idx] = getAccess2(File,time,voltage,1);
    fprintf('\t');
    [~,driving_idx] = getDriving2(File,time,voltage,1);
% else
%     access_idx = varargin{1};
%     driving_idx = varargin{2};
% end

%% Capacitanc
fprintf('\tCapacitance...');
startTime = tic;
capacitance = [];
cap_time = [];
cap_line = [];
try
    if isAtVoltageCompliance
        capStart_idx = access_idx(1);
        capEnd_idx = driving_idx(1) - cutoff_idx;
    else
        phase1 = access_idx(1):driving_idx(1);
        phase1_len = length(phase1);
        phase1_cutoff = ceil(phase1_len * 0.15);
        capStart_idx = access_idx(1) + phase1_cutoff;
        capEnd_idx = driving_idx(1) - phase1_cutoff;
    end
    cap_idx = capStart_idx:capEnd_idx;
    cap_time = time(cap_idx);
    cap_voltage = voltage(cap_idx);
    [slope,intercept,r2] = getLinReg(cap_time,cap_voltage);
    cap_slope = slope * 1e6;
    if r2 > capCheck || isAtVoltageCompliance
        capacitance = amplitude_A / abs(cap_slope) * F_TO_NF;
        cap_line = slope * cap_time + intercept;
    else
        cap_idx = access_idx(1):driving_idx(1);
        cap_time = time(cap_idx);
        cap_voltage = voltage(cap_idx);
        [slope,intercept,r2] = getLinReg(cap_time,cap_voltage);
        if r2 > capCheck || isAtVoltageCompliance
            capacitance = amplitude_A / abs(cap_slope) * F_TO_NF;
            cap_line = slope * cap_time + intercept;
        end

        % % Try exponential fitting: V(t) = V0*(1 - exp(-t/RC))
        % % Normalize to start from zero
        % t0 = cap_time(1);
        % cap_time_fit = cap_time - t0;
        % V0 = cap_voltage(end);
        % fitfun = @(b, t) V0 * (1 - exp(-t / b(1)));  % b(1) = tau = RC
        % errfun = @(b) sum((cap_voltage - fitfun(b, cap_time_fit)).^2);
        % opts = optimset('Display', 'off');
        % b_fit = fminsearch(errfun,1e-6, opts);  % Initial guess: 1 us
        % 
        % tau_fit = b_fit(1);
        % cap_line = fitfun(b_fit, cap_time_fit);
        % capacitance = tau_fit * 1e12;  % C = tau when R = 1 Ohm for estimate
    end
catch
    capacitance = 0;
end
varargout{1} = capacitance;
varargout{2} = cap_time;
varargout{3} = cap_line;

[endTime,unit] = getEndTime(startTime);
if any(capacitance)
    fprintf('FOUND \t\t(%.2f %s)\n',endTime,unit);
else
    fprintf('NONE \t\t(%.2f %s)\n',endTime,unit);
end

end