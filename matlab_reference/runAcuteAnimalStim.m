function [file,quitProgram] = runAcuteAnimalStim(file,name)
%% Constants
OWNER = 'ckn140030@utdallas.edu';
SAVE_PATH = 'Z:\2_ Projects and Data\SBIR Project\Data\Phase_II\AnimalStudy';
SURFACE_AREA = 2000;
BLACKROCK_TO_PLEXON_OMNETICS = [9 10 11 12 13 14 15 16 8 7 6 5 4 3 2 1];
NUM_OF_CHANNELS = 4;

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
subjectSelect = file.Subject;
week = file.Week;

% File
foldername = sprintf('%s_W%02d',subjectSelect,week);
filename = sprintf('%s_%s_%s',notebook,foldername,name);
filepath = fullfile(SAVE_PATH,subjectSelect,foldername,name);
isFilepathExist = exist(filepath,'dir');
if isFilepathExist == 0
    mkdir(filepath);
else
    count = 1;
    while isFilepathExist ~= 0
        count = count + 1;
        name_new = sprintf('%s%d',name,count);
        filepath = fullfile(SAVE_PATH,subjectSelect,foldername,name_new);
    end
    mkdir(filepath);
end

% Geometric surface area conversion
geomSurfaceArea_m2 = SURFACE_AREA * (MICRO_TO_N)^2;      % geometric surface area um2 to m2
geomSurfaceArea_cm2 = geomSurfaceArea_m2 * (N_TO_CENTI)^2;  % geometric surface area m2 to cm2

%% Prepare stimulation for each channel
% Stimulation parameters
stimParam = file.Parameters;
amplitude1_array = stimParam.Amplitude1;    % first phase amplitude
phaseWidth1 = stimParam.PhaseWidth1;        % first phase pulse width
interphaseDelay = stimParam.InterphaseDelay;% interphase delay
amplitude2_array = stimParam.Amplitude2;    % second phase amplitude
phaseWidth2 = stimParam.PhaseWidth2;        % second phase pulse width
stimRate = stimParam.StimulationRate;       % stimulation rate
numOfPulses = stimParam.NumberOfPulses;     % number of pulses

% Time at max potential
phaseWidth1_s = phaseWidth1 * MICRO_TO_N;           % pulse width us to s

%% Stimulation
startTime = tic;
for channelNum = 1:NUM_OF_CHANNELS    % consecutively stimulate channels
    % Channel connection
    fprintf('Blackrock Channel %d...',channelNum);
    channelStim = BLACKROCK_TO_PLEXON_OMNETICS(channelNum);
    fprintf('Plexon Channel %d.\n',channelStim);
    amplitude1 = amplitude1_array(channelNum);
    amplitude2 = amplitude2_array(channelNum);
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

    setDefaultScopeView(scope,amplitude1,1);

    %% Loop through different currents on each channel
    beep;
    startChannelTime = tic;

    % Set rectangular pulse parameters
    pattern.A1 = amplitude1;   	    % first phase amplitude
    pattern.A2 = amplitude2; 	    % second phase amplitude
    pattern.W1 = phaseWidth1;    	% first phase width
    pattern.W2 = phaseWidth2;     	% second phase width
    pattern.Delay = interphaseDelay;% interphase delay

    % Set stimulation parameters
    [quitProgram] = setStimParam(1,channelStim,pattern);
    if quitProgram == true
    end

    % Load parameters to channel
    [quitProgram] = loadChannel(1,channelStim);
    if quitProgram == true
    end
    %         fprintf('\n');

    % Start Stimulation
    % first phase stimulation
    fprintf('Applying %.2f uA for %d us to Channel %d.\n',...
        amplitude1,...
        phaseWidth1,...
        channelNum);
    [quitProgram] = startStimChannel(1,channelStim);
    if quitProgram == true
        return;
    end

    % Charge per phase
    amplitude1_A = amplitude1 * MICRO_TO_N;       % current uA to A
    amplitude1_nA = amplitude1_A * N_TO_NANO;     % current A to nA
    chargePhase = amplitude1_nA * phaseWidth1_s; % charge per phase nC/ph
    fprintf('Qph = %g nC/ph\n',chargePhase);

    % Charge injection
    amplitude1_uA = amplitude1_A * N_TO_MICRO;                       % current A to uA
    chargeInj_uA = amplitude1_uA * phaseWidth1_s / geomSurfaceArea_cm2;   % charge injection uC/cm2
    fprintf('Qinj = %g uC/cm2\n',chargeInj_uA);

    % Capture waveform
    pause(5);
    status = 'Good';
    [...
        voltage,...                 % voltage (V)
        current,...                 % current (uA)
        time,...                    % time (s)
        voltageDrive,...            % driving voltage
        maxPotential,...        % max potential
        isAtVoltageCompliance,...                % is channel broken?
        isPotentialLimitReached,... % is potential limit reached?
        vtPlot]...
        = getAnimalWaveformData(...
        scope,...                           % oscilloscope
        channelNum,...                      % channel
        amplitude1,...                  % present current stimulation
        chargePhase);                  % charge-per-phase

    % Bad status
    if isAtVoltageCompliance % stop at potential limit
        status = 'Voltage compliance reached';
        isPotentialLimitReached = true;
        fprintf('OK.\n');
    end
    if isPotentialLimitReached % stop at potential limit
        fprintf('Potential limit reached...');
        status = 'Cathodic potential limit reached';
        fprintf('OK.\n');
    end

    % Stop stimulation channel
    [quitProgram] = stopStimChannel(1,channelStim);
    if quitProgram == true
        return;
    end
    %     fprintf('\n');

    %% Store data in structure
    data(channelNum).Channel = channelNum;                  %#ok<*AGROW> % channel number
    data(channelNum).Time = time;                           % time
    data(channelNum).VoltageTransient = voltage;            % voltage transient
    data(channelNum).amplitude1ulation = current;          % current stimulation
    data(channelNum).ChargePhase = chargePhase;       	    % charge per phase
    data(channelNum).ChargeInjection = chargeInj_uA;        % charge injection
    data(channelNum).MaxPotentialExcursion = maxPotential;  % max potential
    data(channelNum).DrivingVoltage = voltageDrive;         % driving voltage
    dateTime = getDateTime();
    data.DateTime = dateTimeCreated;
    data(channelNum).DateTime = dateTime;                   % date and time completed
    data(channelNum).Status = status;                       % status

    %% Saving files
    saveAnimalChannelData(...
        filepath,...
        filename,...
        channelNum,...
        data,...
        vtPlot);

    %% Change channel
    endTime = changeAnimalChannel(...
        channelNum,...
        startTime,...
        startChannelTime);
    %     fprintf('\n');
end

%% Saving files
file.Data = data;
dateTimeModified = getDateTime();
file.DateTimeModified = dateTimeModified;
file.DateTimeModified = dateTimeModified;
[file_mat,file_xlsx] = saveAnimalData(...
    filepath,...
    filename,...
    data);
% fprintf('\n');

emailFiles = {file_mat,file_xlsx};
sendEmail(subjectSelect,emailAddress,endTime,emailFiles,'Animal Acute');
if ~strmpi(emailAddress,OWNER)
    sendEmail(subjectSelect,OWNER,endTime,emailFiles,'Animal Acute');
end
fprintf('\n');

end