function [file,quitProgram] = runCharPulsingStim(file,isPulsing,isTargetMax)
%% Constants
OWNER = 'ckn140030@utdallas.edu';
SAVE_PATH = 'Z:\2_ Projects and Data\SBIR Project\Data\Phase_II\MaxPulsing';
SURFACE_AREA = 2000;
BLACKROCK_TO_PLEXON_OMNETICS = [9 10 11 12 13 14 15 16 8 7 6 5 4 3 2 1];
NUM_OF_CHANNELS = 16;
LOWER_POTENTIAL = -0.8;     % lower potential vs. PtIr
% UPPER_POTENTIAL = 0.6;     % lower potential vs. PtIr
DEPOL_TIME = 12e-6;	        % 12 us after first phase

% Conversions
N_TO_CENTI = 1e2;
MICRO_TO_N = 1e-6;
N_TO_MICRO = 1e6;
N_TO_NANO = 1e9;

% Rounding
SIG_FIG = 3;

%% Variables
dateTimeCreated = getDateTime();
file.DateTimeCreated = dateTimeCreated;
notebook = file.Notebook;
serial = file.SerialNumber;
serial_fix = strrep(serial,': ','');
foldername = strrep(serial_fix,'-','_');
scope = file.Oscilloscope.Object;
if isPulsing
    [count,~] = size(file.Pulsing);
    idx = count - 1;
    pulseNum = file.PulsingData(count).PulseNumber;
    pulsingPeriod = file.Pulsing.PulsePeriod;
    chargePhase_array = zeros(1,NUM_OF_CHANNELS);
    chargeInj_array = zeros(1,NUM_OF_CHANNELS);
else
    count = 1;
end
% File
filename = sprintf('%s_%s_%03dx%g',notebook,foldername,idx,pulsingPeriod);
filename = strrep(filename,'+0','');
filepath = fullfile(SAVE_PATH,foldername);
mkdir(filepath);

% Geometric surface area conversion
geomSurfaceArea_m2 = SURFACE_AREA * (MICRO_TO_N)^2;      % geometric surface area um2 to m2
geomSurfaceArea_cm2 = geomSurfaceArea_m2 * (N_TO_CENTI)^2;  % geometric surface area m2 to cm2

%% Prepare stimulation for each channel
% Stimulation parameters
stimParam = file.Parameters;
phaseWidth1 = stimParam.PhaseWidth1;        % first phase pulse width
interphaseDelay = stimParam.InterphaseDelay;% interphase delay
phaseWidth2 = stimParam.PhaseWidth2;        % second phase pulse width
stimRate = stimParam.StimulationRate;       % stimulation rate
numOfPulses = stimParam.NumberOfPulses;     % number of pulses
if isTargetMax
    amplitude1_array = zeros(1,NUM_OF_CHANNELS) + 10;
    amplitude2_array = zeros(1,NUM_OF_CHANNELS) + 10;
else
    amplitude1_data = stimParam.Amplitude1;
    if isstruct(amplitude1_data)
        amplitude1_array = stimParam.Amplitude1.Amplitude;
        amplitude2_array = stimParam.Amplitude2.Amplitude;
    else
        amplitude1_array = stimParam.Amplitude1;
        amplitude2_array = stimParam.Amplitude2;
    end
end

% Time at max potential
phaseWidth1_s = phaseWidth1 * MICRO_TO_N;           % pulse width us to s
maxPotential_time_long = phaseWidth1_s + DEPOL_TIME;% time at max potential
maxPotential_time = round(...   % rounded time at max potential
    maxPotential_time_long,...  % precise time at max potential
    SIG_FIG,...                 % digits to round
    'significant');             % set rounding to significant figure

%% Stimulation
startTime = tic;
for channelNum = 1:NUM_OF_CHANNELS    % consecutively stimulate channels
    % Channel connection
    fprintf('Blackrock Channel %d...',channelNum);
    channelStim = BLACKROCK_TO_PLEXON_OMNETICS(channelNum);

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

    setDefaultScopeView(scope,10,1);
    amplitude1 = amplitude1_array(channelNum);
    %     amplitude2 = amplitude2_array(channelNum);
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
        pattern.W1 = phaseWidth1;    	% first phase width
        pattern.W2 = phaseWidth2;     	% second phase width
        pattern.Delay = interphaseDelay;% interphase delay

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
        fprintf('\tI = %g uA\n',amplitude1_new);

        % Charge per phase
        currentStim_A = currentStim * MICRO_TO_N;       % current uA to A
        currentStim_nA = currentStim_A * N_TO_NANO;  % current A to nA
        chargePhase = currentStim_nA * phaseWidth1_s;   % charge per phase nC/ph
        fprintf('\tQph = %g nC/ph\n',chargePhase);

        % Charge injection
        currentStim_uA = currentStim_A * N_TO_MICRO;                        % current A to uA
        chargeInj = currentStim_uA * phaseWidth1_s / geomSurfaceArea_cm2;   % charge injection uC/cm2
        fprintf('\tQinj = %g uC/cm2\n',chargeInj);
        
        [quitProgram] = startStimChannel(1,channelStim);
        if quitProgram == true
            return;
        end

        % Capture waveform
        pause(5);
        status = 'Good';
        if isCurrentChecked == false
            [...
                voltage,...                 % voltage (V)
                current,...                 % current (uA)
                time,...                    % time (s)
                voltageDrive,...            % driving voltage
                maxPotential,...        % max potential
                isAtVoltageCompliance,...                % is channel broken?
                isPotentialLimitReached,... % is potential limit reached?
                vtPlot,...
                buttonHandle]...            % button handle
                = getAcuteWaveformData(...
                scope,...                           % oscilloscope
                channelNum,...                      % channel
                amplitude1_new,...                  % present current stimulation
                LOWER_POTENTIAL,...                  % potential limits
                chargePhase,...                  % charge-per-phase
                0,...
                1,1,... % monitor scaling
                maxPotential_time,...           % time at max potential
                interphaseDelay,...
                1);                      % number of repeats
            if ~ishandle(buttonHandle)   	% stop by button handle
                status = 'Quit.';
                fprintf('OK.\n');
                break;
            end
            isCurrentChecked = true;
        else
            [...
                voltage,...          	% voltage (V)
                time,...                    % time (s)
                voltageDrive,...            % driving voltage
                maxPotential,...        % max cathodal potential
                isAtVoltageCompliance,...                % is channel broken?
                isPotentialLimitReached,... % is potential limit reached?
                vtPlot,...
                buttonHandle]...            % button handle
                = getVoltageWaveform(...
                scope,...                           % oscilloscope
                channelNum,...                      % channel
                LOWER_POTENTIAL,...                  % potential limits
                chargePhase,...                  % charge-per-phase
                0,...
                1,... % monitor scaling
                maxPotential_time,...           % time at max cathodal potential
                interphaseDelay,...
                1);                       % number of repeats
            if ~ishandle(buttonHandle)   	% stop by button handle
                status = 'Quit.';
                fprintf('OK.\n');
                break;
            end
            if xor(isPotentialLimitReached,isAtVoltageCompliance)...
                    || currentStim >= 1000
                [...
                    voltage,...          	% voltage (V)
                    current,...          	% current (uA)
                    time,...                    % time (s)
                    voltageDrive,...            % driving voltage
                    maxPotential,...        % max cathodal potential
                    isAtVoltageCompliance,...                % is channel broken?
                    isPotentialLimitReached,... % is potential limit reached?
                    vtPlot,...
                    buttonHandle]...            % button handlendle
                    = getAcuteWaveformData(...
                    scope,...                           % oscilloscope
                    channelNum,...                      % channel
                    amplitude1_new,...                  % present current stimulation
                    LOWER_POTENTIAL,...                  % potential limits
                    chargePhase,...                  % charge-per-phase
                    0,...
                    1,1,... % monitor scaling
                    maxPotential_time,...           % time at max potential
                    interphaseDelay,...
                    1);                      % number of repeats
                if ~ishandle(buttonHandle)   	% stop by button handle
                    status = 'Quit.';
                    fprintf('OK.\n');
                    break;
                end
            end
        end

        % Forcing channel stop
        if isAtVoltageCompliance % stop at potential limit
            fprintf('Voltage compliance reached...');
            amplitude1_new = 0;
            amplitude2_new = 0;
            chargeInj = 0;
            chargePhase = 0;
            status = 'Voltage compliance reached';
            fprintf('OK.\n');
        end

        if isPotentialLimitReached % stop at potential limit
            fprintf('Potential limit reached...');
            status = 'Cathodic potential limit reached';
            fprintf('OK.\n');
        end
        %         if ~ishandle(buttonHandle)   	% stop by button handle
        %             status = 'Quit.';
        %             fprintf('OK.\n');
        %             break;
        %         end

        % Adjusting current stimulation
        if isTargetMax
            if ~isAtVoltageCompliance && ~isPotentialLimitReached
                if currentStim < 1000 % break out of loop if on current stimulation limit
                    currentStim = adjustCurrentStim(...
                        SURFACE_AREA,...
                        currentStim,...     % present current stimulation
                        0,...        % step size
                        LOWER_POTENTIAL,...  % lower potential limit
                        maxPotential,...    % max potential
                        0);

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
    amplitude1_array(channelNum) = amplitude1_new;
    amplitude2_array(channelNum) = amplitude2_new;
    chargeInj_array(channelNum) = chargeInj;
    chargePhase_array(channelNum) = chargePhase;
    data(channelNum).Channel = channelNum;                  %#ok<*AGROW> % channel number
    data(channelNum).Time = time;                           % time
    data(channelNum).VoltageTransient = voltage;            % voltage transient
    data(channelNum).CurrentStimulation = current;          % current stimulation
    data(channelNum).ChargePhase = chargePhase;       	    % charge per phase
    data(channelNum).ChargeInjection = chargeInj;        % charge injection
    data(channelNum).MaxPotentialExcursion = maxPotential;  % max potential
    data(channelNum).DrivingVoltage = voltageDrive;         % driving voltage
    dateTime = getDateTime();
    %     data.DateTime = dateTimeCreated;
    data(channelNum).DateTime = dateTime;                   % date and time completed
    data(channelNum).Status = status;                       % status

    %% Saving files
    %     buttonHandle.Visible = 'off';
    delete(buttonHandle);

    saveChannelData(...
        0,...
        filename,...
        channelNum,...
        filepath,...
        data,...
        vtPlot);

    %     saveChannelFig(...
    %         0,...
    %         filename,...
    %         channelNum,...
    %         filepath,...
    %         vtPlot);

    fprintf('\n');

    if stopStim == true  % stop stimulation after current channel
        break;          % quit after this channel
    end
end

%% Saving files
file.Parameters.Amplitude1(count).PulseNumber = pulseNum;
file.Parameters.Amplitude1(count).Amplitude = amplitude1_array;
file.Parameters.Amplitude2(count).PulseNumber = pulseNum;
file.Parameters.Amplitude2(count).Amplitude = amplitude2_array;
file.PulsingData(count).Data = data;
if isTargetMax
    file.PulsingData(count).ChargePhase = chargePhase_array;
    file.PulsingData(count).ChargeInjection = chargeInj_array;
    if count == 1
        file.PulsingData(count).Percentage = zeros(1,NUM_OF_CHANNELS) + 100;
    else
        chargeInj_array_first = file.PulsingData(1).ChargeInjection;
        percentage_array = chargeInj_array ./ chargeInj_array_first * 100;
        percentage_round_array = round(percentage_array);
        file.PulsingData(count).Percentage = percentage_round_array;
    end
end
dateTimeModified = getDateTime();
file.DateTimeModified = dateTimeModified;
file.DateTimeModified = dateTimeModified;
[file_mat,file_xlsx] = saveAcuteData(...
    0,...
    filename,...
    1:NUM_OF_CHANNELS,NUM_OF_CHANNELS,...
    filepath,...
    data);
fprintf('\n');

emailFiles = {file_mat,file_xlsx};
if isTargetMax
    pulseNum_new = makeCommas(pulseNum);
    subject = sprintf('Pulsing Completed: %s Pulses',pulseNum_new);
    subject_fix = strrep(subject,'+','');
else
    
end
emailAddress = file.Email;
sendEmail(serial,emailAddress,endTime,emailFiles,subject_fix);
if ~strcmpi(emailAddress,OWNER)
    sendEmail(serial,OWNER,endTime,emailFiles,subject_fix);
end
fprintf('\n');

end