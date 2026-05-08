function [File,quitProgram] = getSubjectSelect2(File)
%% Constants
% Buttons
BUTTON_NORM = 'Normal';
BUTTON_RATE = 'Rate';
BUTTON_MULTI = 'Multipolar';
BUTTON_EXP = 'EXP';
BUTTON_FIXED = 'FIXED';
BUTTON_MAX = 'MAX';
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
% Options
opts.Default = BUTTON_CONFIRM;       % option dedault
opts.Interpreter = 'tex';   % option LaTeX
% Values
CHARGE_CHECK = 1;
% NUM_OF_ANIMALS = 12;
% NUM_OF_CONTROLS = 8;
% NUM_OF_SUBJECTS = NUM_OF_ANIMALS + NUM_OF_CONTROLS;
BLACKROCK_OMNETICS = transpose(1:16);
% BLACKROCK_TO_PLEXON_OMNETICS = [9;10;11;12;13;14;15;16;1;2;3;4;5;6;7;8];
% BLACKROCK_TO_PLEXON_OMNETICS_NEW = [1;16;7;15;6;14;5;13;4;12;3;11;2;10;1;9];
BLACKROCK_TO_PLEXON_OMNETICS_NEW = [15;13;11;9;7;5;3;1;16;14;12;10;8;6;4;2];
% BLACKROCK_TO_PLEXON_OMNETICS_NEW = BLACKROCK_OMNETICS;

%% Variables
confirmTestType = false;
confirmSubjectSelect = false;
confirmAplitude = false;
quitProgram = false;

%% Mapping
mappingDescription = 'From pad side, wire bundle at bottom';
channelMapping = [ ...
    13 14 15 16; ...
    9 10 11 12; ...
    5 6 7 8; ...
    1 2 3 4];
plexonMapping = zeros(4,4);
amplitudeMapping = zeros(4,4);
chargePhaseMapping = zeros(4,4);
chargeInjectionMapping = zeros(4,4);

%% Subject Selection
% Subject List
animalList = ['SA01';'SA02';'SA03';'PA01';'PA02';'PA03';'PA04';'PA05'];
controlList = ['SC01';'SC02';'SC03';'PC01';'PC02';'PC03'];
animalSerial_start = 7603;
animalSerial = [71 72 73 74 75 76 77 78];
controlSerial_start = 8596;
controlSerial = [2255 2256 2257 2252 2253 2254];
subjectList = [animalList;controlList];
serialList = [animalSerial controlSerial];
subjectList_cell = cellstr(subjectList);
initialChoice = [];
% Subject Select
while confirmSubjectSelect == false
    fprintf('Enter subject selection...');
    % Confirm subject selection
    titleListSubject = 'Subject Selection';      % list title
    % Dialog box
    promptListCh = {...                     % list prompts
        'Select subject configuration.'};  % insrtuction
    [subjectSelect_idx,listSubject_tf] = listdlg(...	% list dialog
        'PromptString',promptListCh,... % list prompts
        'ListString',subjectList_cell,...    % list
        'Name',titleListSubject,...
        'InitialValue',initialChoice,...
        'SelectionMode','single');

    % Collect input
    if listSubject_tf == false             % cancel detected
        fprintf('\nQuitting...'); % quitting
        quitProgram = true;
        break;                     % exit program
    else
        initialChoice = subjectSelect_idx;
    end
    subjectSelect_string = subjectList(subjectSelect_idx,:);
    subjectSelect = char(subjectSelect_string);

    % Confirm subject selection
    % Format questions
    promptQuestSubjectSelect = sprintf('Subject selected: {\\bf%s}',subjectSelect);
    % Question box
    questListSubject = questdlg(...                      % question dialog
        promptQuestSubjectSelect,...                       % question prompts
        'Confirm Subject Selection',...                        % question title
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
        opts);                                      % dialog options
    % Confirmation
    switch questListSubject                              % apply choice
        case BUTTON_CONFIRM                         % check confirmation
            confirmSubjectSelect = true;               % confirm info
            fprintf('%s\n',subjectSelect);  % info confirmed
        case BUTTON_TRY                             % try again
            confirmSubjectSelect = false;               % trying again
            fprintf('\nTrying agin...\n\n');          % starting over
        case BUTTON_CANCEL                          % quit
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            break;                                 % exit program
        otherwise                                   % cancel
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            break;                                 % exit program
    end
end
if quitProgram
    return;
end

%% Test Type
while ~confirmTestType
    fprintf('Select test type...');
    % Question box
    questTestType = questdlg(...
        'Select test type.',...
        'Test Type',...
        BUTTON_NORM,BUTTON_RATE,BUTTON_MULTI,...
        opts);

    choice = sprintf('Test type: {\\bf%s}',questTestType);
    questChoice = questdlg(...
        choice,...
        'Select Test Type',...
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,...
        opts);

    % Confirmation
    switch questChoice                              % apply choice
        case BUTTON_CONFIRM                         % check confirmation
            confirmTestType = true;               % confirm info
            %             fprintf('OK\n');  % info confirmed
        case BUTTON_TRY                             % try again
            confirmTestType = false;               % trying again
            fprintf('\nTrying agin...\n\n');          % starting over
        case BUTTON_CANCEL                          % quit
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            break;                                 % exit program
        otherwise                                   % cancel
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            break;                                 % exit program
    end
end
if quitProgram
    return;
end

% Test Type
switch questTestType                              % apply choice
    case BUTTON_NORM                         % check confirmation
        testType = 'NORM';
        fprintf('normal\n');  % info confirmed
    case BUTTON_RATE                             % try again
        testType = 'RATE';
        fprintf('rate\n');          % starting over
    case BUTTON_MULTI
        testType = 'MULTI';
        fprintf('multipolar\n');
    otherwise                                   % cancel
        fprintf('\nQuitting...');             % quitting
        quitProgram = true;
        return;                                 % exit program
end

%% Amplitude
switch testType
    case 'NORM'
        phaseWidth1 = 200;     % first phase pulse width
        interphaseDelay = 100; % interphase delay
        phaseWidth2 = phaseWidth1;     % second phase pulse width
        stimRate = 50;        % stimulation rate
        while ~confirmAplitude
            fprintf('Select charge type...');
            % Question box
            questChargeType = questdlg(...
                'Select charge type',...
                'Charge Type',...
                BUTTON_MAX,BUTTON_EXP,BUTTON_FIXED,...
                opts);

            choice = sprintf('Charge type: {\\bf%s}',questChargeType);
            questChoice = questdlg(...
                choice,...
                'Charge Type',...
                BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,...
                opts);

            % Confirmation
            switch questChoice                              % apply choice
                case BUTTON_CONFIRM                         % check confirmation
                    confirmAplitude = true;               % confirm info
                    fprintf('%s\n',questChargeType);  % info confirmed
                case BUTTON_TRY                             % try again
                    confirmAplitude = false;               % trying again
                    fprintf('\nTrying agin...\n\n');          % starting over
                case BUTTON_CANCEL                          % quit
                    fprintf('\nQuitting...');             % quitting
                    quitProgram = true;
                    break;                                 % exit program
                otherwise                                   % cancel
                    fprintf('\nQuitting...');             % quitting
                    quitProgram = true;
                    break;                                 % exit program
            end
        end

        switch questChargeType
            case BUTTON_EXP
                type = 'EXP';
                amplitude = ones(4,1);
                charge00 = amplitude * 0;
                charge04 = amplitude * -20;
                charge10 = amplitude * -50;
                charge20 = amplitude * -100;
                switch subjectSelect
                    case {'SA01';'SC01';'PA01';'PA05';'PC01'}
                        amplitude1 = [charge00;charge04;charge10;charge20]; % first phase amplitude
                    case {'SA02';'SC02';'PA02';'PC02'}
                        amplitude1 = [charge20;charge00;charge04;charge10]; % first phase amplitude
                    case {'SA03';'SC03';'PA03';'PC03'}
                        amplitude1 = [charge10;charge20;charge00;charge04]; % first phase amplitude
                    case {'PA04'}
                        amplitude1 = [charge04;charge10;charge20;charge00]; % first phase amplitude
                end
                expAmplitude_arr = amplitude1;
                [expCharge_arr,~] = getCharge(expAmplitude_ar,phaseWidth1);
            case BUTTON_FIXED
                [targetCharge,type,quitProgram] = selectCharge();
%                 charge_C = charge * 1e-9;
%                 phaseWidth_s = 200e-6;
%                 amplitude_A = charge_C / phaseWidth_s;
                targetCharge_arr = ones(16,1) * targetCharge;
                targetAmplitude_arr = -targetCharge_arr / phaseWidth1 * 1e3;
                targetAmplitude_round = round(targetAmplitude_arr);
                amplitude1 = targetAmplitude_round;

            case BUTTON_MAX
                type = 'MAX';
                amplitude = CHARGE_CHECK / phaseWidth1 * 1e3;
                amplitude_round = round(amplitude);
                amplitude1 = ones(16,1) * -amplitude_round;
        end
        amplitude2 = -amplitude1; % second phase amplitude
    case {'RATE','MULTI'}
        type = testType;
        [stimRate,quitProgram] = getStimRateParam(); % stimulation rate
        phaseWidth1 = 25;     % first phase pulse width
        interphaseDelay = 15; % interphase delay
        phaseWidth2 = phaseWidth1;     % second phase pulse width
        if contains2(type,'MULTI')
            [targetCharge,~,quitProgram] = selectCharge();
            targetCharge_arr = ones(16,1) * targetCharge;
            targetAmplitude_arr = -targetCharge_arr / phaseWidth1 * 1e3;
            targetAmplitude_round = round(targetAmplitude_arr );
            amplitude1 = targetAmplitude_round;
        else
            amplitude = 5;
            amplitude_round = round(amplitude);
            amplitude1 = ones(16,1) * -amplitude_round;
            amplitude2 = -amplitude1;
        end
        if quitProgram
            return;
        end
end
if quitProgram
    return;
end

% Multipolar
isMultiTest = contains2(type,'MULTI');
if isMultiTest
    folderpath = fullfile(SAVE_PATH,'AnimalStudy',subjectSelect);
    confirmFile = false;
    while ~confirmFile
        fprintf('Select previous MAT file...');
        [name,path] = uigetfile('*.mat','Previous MAT File',folderpath);
        filepath = fullfile(path,name);
        choice = sprintf('Selected File: {\\bf%s}',name);
        questChoice = questdlg(...
            choice,...
            'MAT File',...
            BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,...
            opts);

        % Confirmation
        switch questChoice                              % apply choice
            case BUTTON_CONFIRM                         % check confirmation
                confirmFile = true;               % confirm info
                PreviousFile = load(filepath);
                close all;
                amplitude_arr = PreviousFile.Parameters.Amplitude1;
                for channelNum = 1:16
                    amplitude = amplitude_arr(channelNum);
                    channel_idx = find(channelMapping == channelNum,1);
                    plexonMapping(channel_idx) = plexonOmnetics(channelNum);
                    amplitudeMapping(channel_idx) = amplitude;
                    if amplitude == 0
                        channelMapping(channel_idx) = 0;
                    end
                end
                File.Parameters.Mapping = channelMapping;
                fprintf('"%s"\n',name);
                %             fprintf('OK\n');  % info confirmed
            case BUTTON_TRY                             % try again
                confirmFile = false;               % trying again
                fprintf('\nTrying agin...\n\n');          % starting over
            case BUTTON_CANCEL                          % quit
                fprintf('\nQuitting...');             % quitting
                quitProgram = true;
                break;                                 % exit program
            otherwise                                   % cancel
                fprintf('\nQuitting...');             % quitting
                quitProgram = true;
                break;                                 % exit program
        end
    end
    if quitProgram
        return;
    end

    confirmMulti = false;
    numOfPoles_raw = '2';
    external_raw = '';
    while ~confirmMulti
        fprintf('Enter multipolar parameters...');
        promptMulti = { ...
            'Number of poles ({\bf0 for common ground, 2 for bipolar, 3 for tripolar, etc}):', ...
            'External return electrode ({\bfOptional}):'};
        defaultMulti = {numOfPoles_raw,external_raw};
        inputMulti = inputdlg( ...
            promptMulti, ...
            'Multipolar Parameters', ....
            [1 75],...
            defaultMulti, ...
            opts);
        if isempty(inputMulti)
            break;
        end
        numOfPols_raw = inputMulti{1};
        numOfPoles = str2double(numOfPols_raw);
        external_raw = inputMulti{2};
        isPt = contains2(external_raw,'Pt') && ~contains2(external_raw,'Ir');
        isPtIr = contains2(external_raw,'Pt') && contains2(external_raw,'Ir');
        isSS = contains2(external_raw,{'SS','stain','steel'});
        if isPt
            external = 'Pt';
        elseif isPtIr
            external = 'PtIr';
        elseif isSS
            external = 'SS';
        end
        numOfPoles_use = sprintf('Number of poles: {\\bd%s}',numOfPoles);
        external_use = sprintf('External electrode: {\\bf%s}',external);
        questMulti = {numOfPoles_use,external_use};
        questChoice = questdlg( ...
            questMulti, ...
            'Multipolar Parameters', ...
            BUTTON_CONFIRM,BUTTON_TRY,BUTTON_QUIT, ...
            opts);
        % Confirmation
        switch questChoice                              % apply choice
            case BUTTON_CONFIRM                         % check confirmation
                confirmMulti = true;               % confirm info
                fprintf('OK\n');
            case BUTTON_TRY                             % try again
                confirmMulti = false;               % trying again
                fprintf('\nTrying agin...\n\n');          % starting over
            case BUTTON_CANCEL                          % quit
                fprintf('\nQuitting...');             % quitting
                quitProgram = true;
                break;                                 % exit program
            otherwise                                   % cancel
                fprintf('\nQuitting...');             % quitting
                quitProgram = true;
                break;                                 % exit program
        end
    end
    if quitProgram
        return;
    end
else
    numOfPoles = 1;
    external = 'PtIr';
end

% Store
device = 'Blackrock';
File.Subject = subjectSelect;	% subject
isAnimal = contains2(subjectSelect,'A');
File.Parameters.Device = device;
File.Parameters.NumberOfChannels = 16;
File.Parameters.Mapping.Description = mappingDescription;
File.Parameters.Mapping.Channel = channelMapping;
File.Parameters.Mapping.Plexon = plexonMapping;
File.Parameters.Mapping.Amplitude = amplitudeMapping;
File.Parameters.Mapping.ChargePhase = chargePhaseMapping;
File.Parameters.Mapping.ChargeInjection = chargeInjectionMapping;
File.Parameters.Channels.(device) = BLACKROCK_OMNETICS;
if isAnimal
    plexonOmnetics = BLACKROCK_TO_PLEXON_OMNETICS_NEW;
else
    plexonOmnetics = BLACKROCK_OMNETICS;
end
File.Parameters.Channels.Plexon = plexonOmnetics;
File.Parameters.SurfaceArea = 5e3 * ones(16,1);
File.Parameters.Type = type;
File.Parameters.NumberOfPoles = numOfPoles;
File.Parameters.External = external;
File.Parameters.Amplitude1 = amplitude1;
File.Parameters.PhaseWidth1 = phaseWidth1;
File.Parameters.InterphaseDelay = interphaseDelay;
File.Parameters.Amplitude2 = amplitude2;
File.Parameters.PhaseWidth2 = phaseWidth2;
File.Parameters.StimulationRate = stimRate;
% isMultiTest = contains2(type,'MULTI');

isRateTest = contains2(type,'RATE');
isTargetMax = contains2(type,'MAX') || isRateTest;
if isTargetMax
    File.Parameters.Target.Charge = Inf;
    File.Parameters.Target.Amplitude = Inf;
    File.Parameters.Target.StepSize = [];
else
    if contains2(type,'nC')
        type = 'FIXED';
    end
    switch type
        case {'FIXED','MULTI'}
            charge = targetCharge_arr;
            amplitude = targetAmplitude_arr;
            stepSize = [];
        case 'EXP'
            charge = expCharge_arr;
            amplitude = expAmplitude_arr;
            stepSize = [];
    end
    File.Parameters.Target.Charge = charge;
    File.Parameters.Target.Amplitude = amplitude;
    File.Parameters.Target.StepSize = stepSize;
end

% Serial Number
len = length(subjectSelect);
switch len
    case 4
        if isAnimal
            serial_start = animalSerial_start;
        else
            serial_start = controlSerial_start;
        end
    case 3
        if isAnimal
            serial_start = animalSerial_start;
        else
            serial_start = controlSerial_start;
        end
end
serial_end = serialList(subjectSelect_idx);
serial = sprintf('%d-%06d',serial_start,serial_end);
File.SerialNumber = serial;
% fprintf('SN%s\n',serial);  % serial

end