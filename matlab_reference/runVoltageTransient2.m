function [File,quitProgram] = runVoltageTransient2(File)
%% Constants
OWNER = 'ckn140030@utdallas.edu';
SAVE_PATH = 'Z:\2_ Projects and Data\SBIR Project\Data\Phase_II\';
SURFACE_AREA = 2000;
BLACKROCK_TO_PLEXON_OMNETICS = [9 10 11 12 13 14 15 16 8 7 6 5 4 3 2 1];
NUM_OF_CHANNELS = 16;
% DEPOL_TIME = 12e-6;	        % 12 us after first phase

% Conversions
N_TO_CENTI = 1e2;
MICRO_TO_N = 1e-6;
N_TO_MICRO = 1e6;
N_TO_NANO = 1e9;

% % Rounding
% SIG_FIG = 3;

%% Variables
File.DateTimeCreated = getDateTime();
Data = File.Data;
notebook = File.Notebook;
serial = File.SerialNumber;
serial_fix = strrep(serial,': ','');
foldername = strrep(serial_fix,'-','_');
scope = File.Oscilloscope.Object;
fields = fieldnames(File);
stimType = File.Parameters.Type;
isTargetMax = strcmpi(stimType,'MAX');
isPulsing = false;
if any(contains(fields,'LongTermPulsing','IgnoreCase',true))
    isPulsing = true;
end
if isPulsing
    [count,~] = size(File.Pulsing);
    idx = count - 1;
    pulseNum = File.PulsingData(count).PulseNumber;
    pulsingPeriod = File.Pulsing.PulsePeriod;
    chargePhase_arr = zeros(1,NUM_OF_CHANNELS);
    chargeInj_arr = zeros(1,NUM_OF_CHANNELS);
    filename = sprintf('%s_%s_%03dx%g',notebook,foldername,idx,pulsingPeriod);
    filename = strrep(filename,'+0','');
    test = 'Pulsing';
    filepath = fullfile(SAVE_PATH,test,foldername);
else
    name = 'AnimalStudy';
    subjectSelect = File.Subject;
    week = File.Week;
    test = File.Test;
    foldername = sprintf('%s_W%02d',subjectSelect,week);
    filename = sprintf('%s_%s_%s',notebook,foldername,test);
    filepath = fullfile(SAVE_PATH,name,subjectSelect,foldername,test);
end
emailFiles = {};

% File path
% isFilepathExist = exist(filepath,'dir');
% if isFilepathExist == 0
%     mkdir(filepath);
% else
%     count = 1;
%     while isFilepathExist ~= 0
%         count = count + 1;
%         name_new = sprintf('%s%d',name,count);
%         filepath = fullfile(SAVE_PATH,subjectSelect,foldername,name_new);
%     end
%     mkdir(filepath);
% end
mkdir(filepath);

% Geometric surface area conversion
geomSurfaceArea_m2 = SURFACE_AREA * (MICRO_TO_N)^2;      % geometric surface area um2 to m2
geomSurfaceArea_cm2 = geomSurfaceArea_m2 * (N_TO_CENTI)^2;  % geometric surface area m2 to cm2

%% Prepare stimulation for each channel
% Stimulation parameters
phaseWidth1 = File.Parameters.PhaseWidth1;        % first phase pulse width
interphaseDelay = File.Parameters.InterphaseDelay;% interphase delay
phaseWidth2 = File.Parameters.PhaseWidth2;        % second phase pulse width
stimRate = File.Parameters.StimulationRate;       % stimulation rate
numOfPulses = File.Parameters.NumberOfPulses;     % number of pulses
if isPulsing
    amplitude1_arr = zeros(1,NUM_OF_CHANNELS) + 10;
    amplitude2_arr = zeros(1,NUM_OF_CHANNELS) + 10;
else
    amplitude1_data = File.Parameters.Amplitude1;
    if isstruct(amplitude1_data)
        amplitude1_arr = File.Parameters.Amplitude1(count).Amplitude;
        amplitude2_arr = File.Parameters.Amplitude2(count).Amplitude;
    else
        amplitude1_arr = File.Parameters.Amplitude1;
        amplitude2_arr = File.Parameters.Amplitude2;
    end
end
pattern = struct(...
    'A1',0,...
    'A2',0,...
    'W1',phaseWidth1,...
    'W1',phaseWidth2,...
    'Delay',interphaseDelay);

% Reference
if any(amplitude1_arr < 0)
    limitPotential = File.ReferenceElectrode.LowerPotential;
else
    limitPotential = File.ReferenceElectrode.UpperPotential;
end

% Time at max potential
phaseWidth1_s = phaseWidth1 * MICRO_TO_N;           % pulse width us to s
% maxPotential_time_long = phaseWidth1_s + DEPOL_TIME;% time at max potential
% maxPotential_time = round(...   % rounded time at max potential
%     maxPotential_time_long,...  % precise time at max potential
%     SIG_FIG,...                 % digits to round
%     'significant');             % set rounding to significant figure

%% Stimulation
startTime = tic;
channelList = 1:NUM_OF_CHANNELS;
for channelNum = channelList   % consecutively stimulate channels
    % Channel connection
    fprintf('Blackrock Channel %d...',channelNum);
    if contains(subjectSelect,'C')
        channelStim = channelNum;
    else
        channelStim = BLACKROCK_TO_PLEXON_OMNETICS(channelNum);
    end
    Data(channelNum).Channel = channelNum;                  %#ok<*AGROW> % channel number
    Data(channelNum).SurfaceArea = SURFACE_AREA;
    fprintf('Plexon Channel %d.\n',channelStim);

    % Set the monitor channel for all stimulators
    [quitProgram] = setMonitorChannel(1,channelStim);
    if quitProgram == true
        return;
    end

    % Set stimulation rate
    [quitProgram] = setStimRate(1,channelStim,stimRate);
    if quitProgram == true
        return;
    end

    % Set number of repetitions for all stimulators
    [quitProgram] = setNumOfPulses(1,channelStim,numOfPulses);
    if quitProgram == true
        return;
    end

    amplitude1 = amplitude1_arr(channelNum);
    setDefaultScopeView2(scope,amplitude1);
    %     amplitude2 = amplitude2_arr(channelNum);
    amplitude1_mag = abs(amplitude1);
    currentStim = amplitude1_mag;
    isChargeMaxFound = false;
    isCurrentChecked = false;
    beep;
    startChannelTime = tic;
    while ~isChargeMaxFound
        % Stimulation change
        if currentStim >= 1000
            currentStim = 1000;
            isAtCurrentCompliance = true;
        else
            isAtCurrentCompliance = false;
        end
        currentChange = currentStim - amplitude1_mag;           % change in amplitude
        amplitude1_unsigned = amplitude1_mag + currentChange;   % unsigned first phase amplitude
        amplitude1_new = -amplitude1_unsigned; % new first phase amplitude
        amplitude2_unsigned = amplitude1_mag + currentChange;   % unsigned second phase amplitude
        amplitude2_new = amplitude2_unsigned; % new second phase amplitude

        % Set rectangular pulse parameters
        pattern.A1 = amplitude1_new;   	% first phase amplitude
        pattern.A2 = amplitude2_new; 	% second phase amplitude

        % Set stimulation parameters
        [quitProgram] = setStimParam(1,channelStim,pattern);
        if quitProgram == true
            return;
        end

        % Load parameters to channel
        [quitProgram] = loadChannel(1,channelStim);
        if quitProgram == true
            fclose(scope);
            closeAllStim();
            return;
        end
        %         fprintf('\n');

        % Start Stimulation
        % first phase stimulation
        %         fprintf('Applying %.2f uA for %d us to Channel %d.\n',...
        %             amplitude1_new,...
        %             phaseWidth1,...
        %             channelNum);
        if amplitude1_new == 0
            fprintf('\tI = 0 uA\n');
        else
            fprintf('\tI = %g uA\n',amplitude1_new);
        end

        % Charge per phase
        currentStim_A = currentStim * MICRO_TO_N;       % current uA to A
        currentStim_nA = currentStim_A * N_TO_NANO;  % current A to nA
        chargePhase = currentStim_nA * phaseWidth1_s;   % charge per phase nC/ph
        File.Data(channelNum).ChargePhase = chargePhase;       	    % charge per phase
        fprintf('\tQph = %g nC/ph\n',chargePhase);

        % Charge injection
        currentStim_uA = currentStim_A * N_TO_MICRO;                        % current A to uA
        chargeInj = currentStim_uA * phaseWidth1_s / geomSurfaceArea_cm2;   % charge injection uC/cm2
        File.Data(channelNum).ChargeInjection = chargeInj;        % charge injection
        fprintf('\tQinj = %g uC/cm2\n',chargeInj);

        % Stimulation
        [quitProgram] = startStimChannel(1,channelStim);
        if quitProgram == true
            return;
        end

        % Capture waveform
        pause(5);
        if isCurrentChecked == false
            [File,buttonHandle]...            % button handle
                = getWaveformData2(File,channelNum,limitPotential,'both');
            if ~ishandle(buttonHandle)   	% stop by button handle
                File.Data(channelNum).Status = 'Quit';
                fprintf('OK.\n');
                break;
            end
            isCurrentChecked = true;
        else
            if strcmpi(stimType,'MAX')
               [File,buttonHandle]...            % button handle
                = getWaveformData2(File,channelNum,limitPotential,'CH1');
                if ~ishandle(buttonHandle)   	% stop by button handle
                    File.Data(channelNum).Status = 'Quit';
                    fprintf('OK.\n');
                    break;
                end
                if xor(isPotentialLimitReached,isAtVoltageCompliance)...
                        || currentStim >= 1000
                    [File,buttonHandle]...            % button handle
                        = getWaveformData2(File,channelNum,limitPotential,'both');
                    if ~ishandle(buttonHandle)   	% stop by button handle
                        File.Data(channelNum).Status = 'Quit';
                        fprintf('OK.\n');
                        break;
                    end
                end
            end
        end

        % Forcing channel stop
        status = File.Data(channelNum).Status;
        if contains(status,'voltage','IgnoreCase',true) % stop at potential limit
            fprintf('Voltage compliance reached...');
            
            fprintf('OK.\n');
        elseif contains(status,'limit','IgnoreCase',true) % stop at potential limit
            fprintf('Potential limit reached...');
            fprintf('OK.\n');
        end
        %         if ~ishandle(buttonHandle)   	% stop by button handle
        %             status = 'Quit.';
        %             fprintf('OK.\n');
        %             break;
        %         end

        
        % Adjusting current stimulation
        elapsedChannelTime = toc(startChannelTime);
        if elapsedChannelTime > 600
            fprintf('Took too long...');
            status = 'Quit';
            isChargeMaxFound = true;
            fprintf('OK.\n');
        end
        if isTargetMax
            if ~(isAtVoltageCompliance || isPotentialLimitReached)
                if currentStim < 1000 % break out of loop if on current stimulation limit
                    currentStim = adjustCurrentStim(...
                        SURFACE_AREA,...
                        currentStim,...     % present current stimulation
                        0,...        % step size
                        limitPotential,...  % lower potential limit
                        maxPotential,...    % max potential
                        Inf);

                    if isAtCurrentCompliance && currentStim >= 1000
                        fprintf('Current compliance reached...');
                        status = 'Current compliance reached';
                        fprintf('OK.\n');
                        break;
                    end
                else
                    isChargeMaxFound = true;
                end
            else
                isChargeMaxFound = true;
            end
        else
            isChargeMaxFound = true;
        end
    end

    % Stop stimulation channel
    [quitProgram] = stopStimChannel(1,channelStim);
    if quitProgram == true
        return;
    end
    %     fprintf('\n');

    %% Change channel
    [~,stopStim,endTime] = changeChannel(...
        1,...
        channelNum,NUM_OF_CHANNELS,...
        startTime,startChannelTime,...
        1);
    %     fprintf('\n');

    %% Store data in structure
    if isPulsing
        amplitude1_arr(channelNum) = amplitude1_new;
        amplitude2_arr(channelNum) = amplitude2_new;
        chargeInj_arr(channelNum) = chargeInj;
        chargePhase_arr(channelNum) = chargePhase;
    end
    Data(channelNum).Time = time;                           % time
    Data(channelNum).Voltage = voltage;            % voltage transient
    Data(channelNum).Current = current;          % current stimulation
    Data(channelNum).PotentialExcursion = maxPotential;  % max potential
    Data(channelNum).DrivingVoltage = voltageDrive;         % driving voltage
    dateTime = getDateTime();
    %     Data.DateTime = dateTimeCreated;
    Data(channelNum).DateTime = dateTime;                   % date and time completed
    Data(channelNum).Status = status;                       % status
    delete(buttonHandle);
    Data(channelNum).Figure = vtPlot;

    %% Saving files
    %     buttonHandle.Visible = 'off';
%     delete(buttonHandle);
    file_tif = saveChannelData2(...
        Data,...
        channelNum,...
        isAtVoltageCompliance,...
        filename,...
        filepath);
    emailFiles_alloc = [emailFiles,file_tif];
    emailFiles = emailFiles_alloc;

    fprintf('\n');

    if stopStim == true  % stop stimulation after current channel
        break;          % quit after this channel
    end
end

%% Saving files
if isPulsing
    File.Parameters.Amplitude1(count).PulseNumber = pulseNum;
    File.Parameters.Amplitude1(count).Amplitude = amplitude1_arr;
    File.Parameters.Amplitude2(count).PulseNumber = pulseNum;
    File.Parameters.Amplitude2(count).Amplitude = amplitude2_arr;
    File.PulsingData(count).Data = Data;
else
    File.Parameters.Amplitude1 = amplitude1_arr;
    File.Parameters.Amplitude2 = amplitude2_arr;
    File.Data = Data;
end

if isPulsing
    File.PulsingData(count).ChargePhase = chargePhase_arr;
    File.PulsingData(count).ChargeInjection = chargeInj_arr;
    if istargetMax
        if count == 1
            File.PulsingData(count).Percentage = zeros(1,NUM_OF_CHANNELS) + 100;
        else
            chargeInj_arr_first = File.PulsingData(1).ChargeInjection;
            percentage_arr = chargeInj_arr ./ chargeInj_arr_first * 100;
            percentage_round_arr = round(percentage_arr);
            File.PulsingData(count).Percentage = percentage_round_arr;
        end
    end
end
dateTimeModified = getDateTime();
File.DateTimeModified = dateTimeModified;
[~,file_xlsx] = saveAcuteData2(...
    File,...
    filename,...
    filepath);
fprintf('\n');

emailFiles_alloc = [emailFiles,file_xlsx];
emailFiles = emailFiles_alloc;
if isPulsing
    pulseNum_new = addCommas(pulseNum);
    subject = sprintf('%s Pulses %s W%02d',pulseNum_new);
    subject = strrep(subject,'+','');
else
    subject = sprintf('VT %s W%02d',subjectSelect,week);
end
emailAddress = File.Email;
sendEmail(subjectSelect,emailAddress,endTime,emailFiles,subject);
if ~strcmpi(emailAddress,OWNER)
    sendEmail(subjectSelect,OWNER,endTime,emailFiles,subject);
end
fprintf('\n');

end